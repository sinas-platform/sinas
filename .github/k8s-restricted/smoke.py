#!/usr/bin/env python3
"""End-to-end smoke checks against a Sinas install in a namespace that
enforces Pod Security "restricted" (.github/workflows/k8s-restricted.yml).

Standard library only. Talks to the backend through a port-forward:

  SINAS_URL=http://127.0.0.1:18000 SINAS_EMAIL=... SINAS_PASSWORD=... \
  EXPECT_UID=10001 python3 smoke.py

Each check is a function named check_*; they run in definition order and
the script exits non-zero if any fails. Add further end-to-end checks
(pipelines, sub-agents, ...) as more check_* functions. SMOKE_PROFILE (the
workflow's matrix profile) selects the checks marked with @profiles(...);
unmarked checks run everywhere.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
import urllib.error
import urllib.request

URL = os.environ.get("SINAS_URL", "http://127.0.0.1:18000").rstrip("/")
EMAIL = os.environ["SINAS_EMAIL"]
PASSWORD = os.environ["SINAS_PASSWORD"]
EXPECT_UID = int(os.environ.get("EXPECT_UID", "10001"))
NS = "k8s_ci"
RELEASE = os.environ.get("SINAS_RELEASE", "sinas")
PROFILE = os.environ.get("SMOKE_PROFILE", "split")
# Profiles that run user code (features.codeExecution on).
EXEC_PROFILES = ("split", "lite")


def profiles(*names: str):
    """Run the decorated check only in these profiles."""

    def mark(fn):
        fn.profiles = names
        return fn

    return mark

_token: str | None = None


def call(method: str, path: str, body: dict | None = None, timeout: float = 60) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(URL + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if _token:
        req.add_header("Authorization", f"Bearer {_token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw.decode(errors="replace")}


def check_health():
    deadline = time.time() + 120
    while True:
        try:
            status, body = call("GET", "/health", timeout=5)
            if status == 200:
                return
        except OSError as e:
            body = str(e)
        if time.time() > deadline:
            raise AssertionError(f"/health not OK: {body}")
        time.sleep(3)


def check_login():
    global _token
    status, body = call("POST", "/auth/login", {"email": EMAIL, "password": PASSWORD})
    assert status == 200 and body.get("access_token"), f"login failed: {status} {body}"
    _token = body["access_token"]


# What the function reports about the process it runs in. `six` is approved
# as a platform dependency below, so the sandbox pod pip-installs it at
# creation, as the non-root user, into the executor image's group-writable
# site-packages.
WHOAMI = '''
import os, socket, tempfile

def handler(input, context):
    out = {
        "uid": os.getuid(),
        "gid": os.getgid(),
        "home": os.environ.get("HOME"),
        # the pod it ran in: sinas-sbx-<execution> / sinas-trusted-worker-<n>
        "hostname": socket.gethostname(),
    }
    with tempfile.NamedTemporaryFile(dir="/tmp") as f:
        f.write(b"ok")
    out["tmp_writable"] = True
    if input.get("import_dependency"):
        import six
        out["six"] = six.__version__
        out["six_file"] = six.__file__
    return out
'''


def _create_function(name: str, shared_pool: bool):
    call("DELETE", f"/api/v1/functions/{NS}/{name}")
    status, body = call("POST", "/api/v1/functions", {
        "namespace": NS,
        "name": name,
        "description": "Pod Security smoke test",
        "code": WHOAMI,
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "shared_pool": shared_pool,
    })
    assert status in (200, 201), f"create {name}: {status} {body}"


def _execute(name: str, payload: dict) -> dict:
    status, body = call(
        "POST", f"/functions/{NS}/{name}/execute",
        {"input": payload, "timeout": 300}, timeout=330,
    )
    assert status == 200, f"execute {name}: HTTP {status} {body}"
    assert body.get("status") == "success", f"execute {name}: {body}"
    result = body["result"]
    print(f"    {name} -> {json.dumps(result)}")
    return result


def check_clickhouse_off():
    """The CI values run without ClickHouse: the API says so, the request-log
    API answers 503 with the reason instead of erroring, and nothing else
    depends on it."""
    status, body = call("GET", "/info")
    assert status == 200, f"/info: {status} {body}"
    assert body["features"]["clickhouse"] is False, body["features"]
    status, body = call("GET", "/api/v1/request-logs")
    assert status == 503 and "disabled" in body.get("detail", ""), f"request-logs: {status} {body}"


def _system_health() -> dict:
    status, body = call("GET", "/api/v1/system/health?include_stats=true", timeout=90)
    assert status == 200, f"system health: {status} {body}"
    assert body["deployment"] == "kubernetes", body["deployment"]
    return body


@profiles(*EXEC_PROFILES)
def check_system_page_lists_release_pods():
    body = _system_health()
    assert body["services_enabled"] is True, body
    assert body["services_unavailable_reason"] is None, body["services_unavailable_reason"]
    assert any(s["service"] == "backend" for s in body["services"]), body["services"]


def _timed(path: str) -> tuple[float, dict]:
    t = time.monotonic()
    status, body = call("GET", path, timeout=30)
    elapsed = time.monotonic() - t
    assert status == 200, f"{path}: {status} {body}"
    return elapsed, body


def _rest_of_page_present(body: dict) -> None:
    """Everything that doesn't come from the Kubernetes API is there."""
    assert body["host"].get("disk_total_gb") is not None, body["host"]
    assert body["redis"] is not None and body["redis"].get("used_bytes") is not None, body["redis"]
    assert isinstance(body["warnings"], list), body
    assert body["executors"], body


@profiles("noexec")
def check_system_page_pod_list_off():
    """systemStats.rbac=false: no API call, no error; the cluster part says
    why it isn't there, the rest of the page is, and quickly."""
    for path in ("/api/v1/system/health?include_stats=false", "/api/v1/system/health?include_stats=true"):
        elapsed, body = _timed(path)
        assert elapsed < 3, f"{path} took {elapsed:.1f}s"
        assert body["deployment"] == "kubernetes", body["deployment"]
        assert body["services_enabled"] is False, body
        assert body["services"] == [] and body["services_unavailable_reason"] is None, body
        assert "turned off" in (body.get("cluster_unavailable_reason") or ""), body
        _rest_of_page_present(body)


@profiles("blocked-api")
def check_system_page_without_reachable_api():
    """RBAC granted, but the apiserver drops the backend's traffic (the
    workflow points the backend at an unroutable apiserver address). The
    first poll waits at most the breaker's budget; after that every poll of
    the health endpoint and the executor cards is answered from the breaker
    at once, with the reason, and the rest of the page is unaffected."""
    elapsed, body = _timed("/api/v1/system/health?include_stats=true")
    assert elapsed < 4, f"first health call took {elapsed:.1f}s"
    assert body["services_enabled"] is True, body
    reason = body.get("cluster_unavailable_reason") or ""
    assert reason and "Traceback" not in reason, body
    _rest_of_page_present(body)
    time.sleep(3)  # the background call has hit its connect timeout by now
    for _ in range(3):
        for path in (
            "/api/v1/system/health?include_stats=false",
            "/api/v1/system/health?include_stats=true",
            "/api/v1/containers/stats",
            "/api/v1/workers/count",
            "/api/v1/queue/stats",
        ):
            elapsed, body = _timed(path)
            assert elapsed < 1.5, f"{path} took {elapsed:.1f}s with the breaker open"
            if path.startswith("/api/v1/system/health"):
                assert body.get("cluster_unavailable_reason"), body
                assert body.get("cluster_retry_after_seconds"), body
                _rest_of_page_present(body)
            elif path != "/api/v1/queue/stats":
                assert body.get("cluster_unavailable") is True, (path, body)
                assert body.get("retry_after_seconds"), (path, body)
    print(f"  reason: {body.get('unavailable_reason') or reason}")


@profiles("noexec")
def check_code_execution_off():
    status, body = call("GET", "/info")
    assert body["features"]["code_execution"] is False, body["features"]
    status, body = call("POST", "/api/v1/functions", {
        "namespace": NS, "name": "nope", "description": "must be refused", "code": WHOAMI,
        "input_schema": {"type": "object"}, "output_schema": {"type": "object"},
    })
    assert status == 403 and "disabled" in json.dumps(body), f"create function: {status} {body}"
    h = {"sandbox": "/api/v1/containers/stats", "shared": "/api/v1/workers/count"}
    for card, path in h.items():
        status, body = call("GET", path)
        assert status == 200, f"{card}: {status} {body}"
        assert "Code execution is disabled" in (body.get("unavailable_reason") or ""), (card, body)


@profiles(*EXEC_PROFILES)
def check_dependency_approved():
    status, body = call("POST", "/api/v1/dependencies", {"package_name": "six"})
    assert status in (200, 201) or (status == 400 and "already" in json.dumps(body)), (
        f"approve dependency: {status} {body}"
    )


@profiles(*EXEC_PROFILES)
def check_sandbox_function_runs_in_restricted_pod():
    _create_function("whoami", shared_pool=False)
    result = _execute("whoami", {"import_dependency": True})
    assert result["uid"] == EXPECT_UID, result
    assert result["gid"] == 0, result
    assert result["tmp_writable"] is True, result
    # ran in a single-use sandbox pod, not in-process or in a trusted worker
    assert result["hostname"].startswith("sinas-sbx-"), result
    # installed into the image's (group-writable) system site-packages, not
    # pip's --user fallback under $HOME
    assert result.get("six"), result
    assert result["six_file"].startswith("/usr/local/"), result


@profiles(*EXEC_PROFILES)
def check_trusted_function_runs_in_restricted_pod():
    _create_function("whoami_trusted", shared_pool=True)
    result = _execute("whoami_trusted", {})
    assert result["uid"] == EXPECT_UID, result
    assert result["gid"] == 0, result
    # ran in a warm trusted-worker pod (k8s_shared), not in-process
    assert result["hostname"].startswith(f"{RELEASE}-trusted-worker-"), result


def main() -> int:
    checks = [
        (n, f)
        for n, f in globals().items()
        if n.startswith("check_") and callable(f) and PROFILE in getattr(f, "profiles", (PROFILE,))
    ]
    print(f"profile {PROFILE}: {', '.join(n for n, _ in checks)}")
    failed = 0
    for name, fn in checks:
        t = time.time()
        try:
            fn()
            print(f"PASS {name} ({time.time() - t:.1f}s)", flush=True)
        except Exception:
            failed += 1
            print(f"FAIL {name} ({time.time() - t:.1f}s)", flush=True)
            traceback.print_exc()
            if name in ("check_health", "check_login"):
                break
    print(f"{len(checks) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
