#!/usr/bin/env python3
"""Structural checks on `helm template` output for .github/workflows/helm.yml.

    python check_chart_renders.py <check> <rendered.yaml> [<control.yaml>]

Each check asserts a property of one rendered configuration AND, where a
control render is given (the default profile), that the same assertion
would fail there: a check that can't tell the two apart proves nothing.
Needs PyYAML.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

import yaml

# Workloads running Sinas code (they all get backendEnv), split profile.
SINAS_WORKLOADS = {
    "backend",
    "queue-worker",
    "queue-agent",
    "queue-agent-sub",
    "queue-pipeline",
    "scheduler",
    "cdc-worker",
}


def load(path: str) -> list[dict]:
    with open(path) as f:
        return [d for d in yaml.safe_load_all(f) if d]


def by_kind(docs: list[dict], kind: str) -> dict[str, dict]:
    return {d["metadata"]["name"]: d for d in docs if d["kind"] == kind}


def pod_specs(docs: list[dict]) -> dict[str, dict]:
    return {
        d["metadata"]["name"]: d["spec"]["template"]["spec"]
        for d in docs
        if d["kind"] in ("Deployment", "StatefulSet")
    }


def containers(spec: dict) -> list[dict]:
    return spec.get("initContainers", []) + spec["containers"]


def env_of(container: dict) -> dict[str, dict]:
    return {e["name"]: e for e in container.get("env", [])}


def sinas_containers(docs: list[dict]) -> list[tuple[str, dict, dict]]:
    """(workload, pod spec, container) for every container that gets backendEnv."""
    out = []
    for name, spec in pod_specs(docs).items():
        for c in containers(spec):
            if "DATABASE_DIRECT_HOST" in env_of(c):
                out.append((name, spec, c))
    return out


def release_secret(docs: list[dict]) -> dict:
    return next(d for n, d in by_kind(docs, "Secret").items() if n.endswith("-secrets"))


# ── checks ───────────────────────────────────────────────────────────


def existing_secret(docs: list[dict]) -> None:
    """secrets.existingSecret=app-keys (secretKeyKey=sk): the keys are never
    rendered, every consumer reads them from app-keys."""
    data = release_secret(docs).get("stringData", {})
    assert "secret-key" not in data and "encryption-key" not in data, data.keys()
    found = sinas_containers(docs)
    assert {w for w, _, _ in found} == SINAS_WORKLOADS, sorted(w for w, _, _ in found)
    for workload, _, c in found:
        env = env_of(c)
        sk = env["SECRET_KEY"]["valueFrom"]["secretKeyRef"]
        ek = env["ENCRYPTION_KEY"]["valueFrom"]["secretKeyRef"]
        assert sk == {"name": "app-keys", "key": "sk"}, (workload, sk)
        assert ek == {"name": "app-keys", "key": "encryption-key"}, (workload, ek)


def code_execution_off(docs: list[dict]) -> None:
    """features.codeExecution=false: no executor RBAC or settings; only the
    backend keeps an API identity (System page pod list, systemStats on)."""
    roles = set(by_kind(docs, "Role"))
    sas = set(by_kind(docs, "ServiceAccount"))
    assert not any(r.endswith("-executor-manager") for r in roles), roles
    assert not any(r.endswith("-executor-manager") for r in by_kind(docs, "RoleBinding"))
    assert not any(s.endswith("-sandbox") for s in sas), sas
    assert any(r.endswith("-system-stats") for r in roles), roles
    for workload, spec, c in sinas_containers(docs):
        env = env_of(c)
        leaked = [n for n in env if n.startswith("K8S_SANDBOX_") or n in (
            "FUNCTION_CONTAINER_IMAGE", "K8S_TRUSTED_WORKERS")]
        assert not leaked, (workload, leaked)
        assert env["SANDBOX_EXECUTOR"]["value"] == "disabled", workload
        assert env["TRUSTED_EXECUTOR"]["value"] == "disabled", workload
        assert env["CODE_EXECUTION_ENABLED"]["value"] == "false", workload
        if workload == "backend":
            assert spec.get("serviceAccountName", "").endswith("-executor-manager"), workload
            assert "automountServiceAccountToken" not in spec, workload
        else:
            assert "serviceAccountName" not in spec, workload
            assert spec.get("automountServiceAccountToken") is False, workload


def no_api_identity(docs: list[dict]) -> None:
    """codeExecution=false + systemStats.rbac=false: no ServiceAccount, Role
    or RoleBinding at all, no Sinas pod mounts a token, the backend is told
    not to read the Kubernetes API."""
    for kind in ("ServiceAccount", "Role", "RoleBinding", "ClusterRole", "ClusterRoleBinding"):
        assert not by_kind(docs, kind), (kind, list(by_kind(docs, kind)))
    for name, spec in pod_specs(docs).items():
        if name in SINAS_WORKLOADS | {"console", "pgbouncer"}:
            assert "serviceAccountName" not in spec, name
            assert spec.get("automountServiceAccountToken") is False, name
    for workload, _, c in sinas_containers(docs):
        assert env_of(c)["SYSTEM_STATS_KUBERNETES"]["value"] == "false", workload


def postgres_tls(docs: list[dict]) -> None:
    """postgres.external.tls verify-full + caSecret pg-ca: pgbouncer verifies
    its server connections, the Sinas pods' direct connections do too, the CA
    is mounted wherever it is referenced."""
    specs = pod_specs(docs)
    ca_path = "/etc/sinas/postgres-ca/ca.crt"

    def has_ca(spec: dict, c: dict) -> None:
        mounts = {m["name"]: m for m in c.get("volumeMounts", [])}
        assert mounts["postgres-ca"]["mountPath"] == "/etc/sinas/postgres-ca", mounts
        assert mounts["postgres-ca"].get("readOnly") is True
        vols = {v["name"]: v for v in spec.get("volumes", [])}
        assert vols["postgres-ca"]["secret"] == {
            "secretName": "pg-ca",
            "items": [{"key": "ca.crt", "path": "ca.crt"}],
        }, vols["postgres-ca"]

    pgb = specs["pgbouncer"]
    c = pgb["containers"][0]
    env = env_of(c)
    assert env["SERVER_TLS_SSLMODE"]["value"] == "verify-full", env.keys()
    assert env["SERVER_TLS_CA_FILE"]["value"] == ca_path
    assert "@10.9.8.7:5432/" in env["DATABASE_URL"]["value"]
    has_ca(pgb, c)

    found = sinas_containers(docs)
    assert {w for w, _, _ in found} == SINAS_WORKLOADS, sorted(w for w, _, _ in found)
    for workload, spec, c in found:
        env = env_of(c)
        assert env["DATABASE_DIRECT_HOST"]["value"] == "10.9.8.7", workload
        assert env["DATABASE_DIRECT_SSLMODE"]["value"] == "verify-full", workload
        assert env["DATABASE_DIRECT_SSLROOTCERT"]["value"] == ca_path, workload
        has_ca(spec, c)
    assert "postgres" not in by_kind(docs, "StatefulSet")


def clickhouse_off(docs: list[dict]) -> None:
    """clickhouse.enabled=false: no ClickHouse objects or credentials, every
    Sinas process gets the explicit-disable signal (empty CLICKHOUSE_HOST)."""
    names = {(d["kind"], d["metadata"]["name"]) for d in docs}
    assert not any("clickhouse" in n for _, n in names), sorted(names)
    assert "clickhouse-password" not in release_secret(docs).get("stringData", {})
    found = sinas_containers(docs)
    assert found
    for workload, _, c in found:
        env = env_of(c)
        assert env["CLICKHOUSE_HOST"].get("value", None) == "", (workload, env["CLICKHOUSE_HOST"])
        assert "CLICKHOUSE_PASSWORD" not in env, workload
        assert "CLICKHOUSE_PORT" not in env, workload


# The processes without an HTTP server: heartbeat-file probes.
HEARTBEAT_WORKLOADS = SINAS_WORKLOADS - {"backend"}
PROBE_KINDS = ("startupProbe", "livenessProbe", "readinessProbe")


def _deployment_containers(docs: list[dict]) -> list[tuple[str, dict, dict]]:
    out = []
    for name, d in by_kind(docs, "Deployment").items():
        spec = d["spec"]["template"]["spec"]
        out += [(name, spec, c) for c in spec["containers"]]
    assert out, "no Deployments rendered"
    return out


def probes(docs: list[dict]) -> None:
    """Every Deployment container has readiness + liveness probes; the
    backend a startupProbe (alembic runs before uvicorn); the heartbeat
    workers check the file the process is told to write, on its emptyDir."""
    for name, spec, c in _deployment_containers(docs):
        for kind in ("readinessProbe", "livenessProbe"):
            assert kind in c, (name, c["name"], kind)
        if name == "backend":
            assert c["startupProbe"]["httpGet"]["path"] == "/health", c.get("startupProbe")
        if name in HEARTBEAT_WORKLOADS:
            hb = env_of(c)["SINAS_HEARTBEAT_FILE"]["value"]
            hb_dir = hb.rsplit("/", 1)[0]
            for kind in PROBE_KINDS:
                cmd = c[kind]["exec"]["command"]
                assert cmd[:2] == ["sh", "-c"] and hb in cmd[2] and "stat -c %Y" in cmd[2], (name, kind, cmd)
            mount = next(m for m in c["volumeMounts"] if m["mountPath"] == hb_dir)
            vol = next(v for v in spec["volumes"] if v["name"] == mount["name"])
            assert "emptyDir" in vol, (name, vol)
    names = {n for n, _, _ in _deployment_containers(docs)}
    if "queue-worker" in names:  # split profile: all of them
        assert HEARTBEAT_WORKLOADS <= names, sorted(names)


# Rendered with these overrides in helm.yml (probe-overrides).
PROBE_OVERRIDES = {
    ("backend", "startupProbe", "failureThreshold"): 90,
    ("queue-agent", "livenessProbe", "periodSeconds"): 45,
    ("pgbouncer", "livenessProbe", "timeoutSeconds"): 3,
    ("console", "readinessProbe", "periodSeconds"): 7,
}
HEARTBEAT_MAX_AGE_OVERRIDE = 300  # workerProbes.heartbeatMaxAgeSeconds


def probe_overrides(docs: list[dict]) -> None:
    """Probe values are applied: per-component keys change, the rest keep
    their defaults; workerProbes reaches every heartbeat worker."""
    containers = {name: c for name, _, c in _deployment_containers(docs)}
    for (name, kind, key), value in PROBE_OVERRIDES.items():
        assert containers[name][kind][key] == value, (name, kind, containers[name][kind])
    # untouched keys keep the built-in defaults
    assert containers["backend"]["startupProbe"]["periodSeconds"] == 10
    assert containers["queue-agent"]["livenessProbe"]["failureThreshold"] == 3
    assert containers["queue-worker"]["livenessProbe"]["periodSeconds"] == 30
    for name in HEARTBEAT_WORKLOADS & set(containers):
        for kind in PROBE_KINDS:
            cmd = containers[name][kind]["exec"]["command"][2]
            assert f"-lt {HEARTBEAT_MAX_AGE_OVERRIDE} ]" in cmd, (name, kind, cmd)


CHECKS: dict[str, Callable[[list[dict]], None]] = {
    "existing-secret": existing_secret,
    "code-execution-off": code_execution_off,
    "no-api-identity": no_api_identity,
    "postgres-tls": postgres_tls,
    "clickhouse-off": clickhouse_off,
    "probes": probes,
    "probe-overrides": probe_overrides,
}


def main(argv: list[str]) -> int:
    if len(argv) not in (3, 4) or argv[1] not in CHECKS:
        print(__doc__, f"checks: {', '.join(CHECKS)}", file=sys.stderr)
        return 2
    check = CHECKS[argv[1]]
    check(load(argv[2]))
    print(f"ok   {argv[1]}: {argv[2]}")
    if len(argv) == 4:
        try:
            check(load(argv[3]))
        except (AssertionError, KeyError, StopIteration):
            print(f"ok   {argv[1]}: control {argv[3]} fails it, as it must")
        else:
            print(f"FAIL {argv[1]}: control {argv[3]} passes too — the check proves nothing")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
