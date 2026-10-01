"""Single-use sandbox Pods on Kubernetes.

k8s counterpart of `executor._ephemeral_runtime`: create a hardened Pod from
the executor image, speak the same request/trigger/result-file IPC against its
in-container executor daemon, then delete the Pod. Used by
`K8sPodSandboxExecutor` (untrusted function execution) and `code_execution`
(agent codeExecution).

Differences from the Docker runtimes, by nature of the platform:
- No image build: the executor image must be pullable by the cluster
  (`k8s_sandbox_image`, falling back to `function_container_image`). Extra
  packages from the `Dependency` table are pip-installed into each pod at
  create time (mirroring the pool's `_install_packages`) unless
  `k8s_sandbox_install_dependencies=false` — set that when the packages are
  baked into a custom image.
- Network isolation is not created here: the Helm chart ships a NetworkPolicy
  selecting `sinas.type=sandbox-executor` pods (internet egress only, no
  cluster-internal traffic). The Docker `sandbox_network` has no per-pod
  equivalent to create on the fly.
- No pids_limit (a kubelet-level setting, not per-pod).
- Credentials come from the surrounding pod's ServiceAccount, which needs
  create/get/delete + exec on pods in the sandbox namespace. Sandbox pods
  themselves get `automountServiceAccountToken: false`.

The kubernetes client is blocking; callers run these helpers via
`asyncio.to_thread`. The SDK is imported lazily so this module is importable
in non-k8s deployments.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings

logger = logging.getLogger(__name__)

_POD_LABELS = {"sinas.type": "sandbox-executor", "sinas.ephemeral": "true"}
_DEPS_INSTALL_TIMEOUT = 300  # seconds for the in-pod pip install
_clients: tuple[Any, Any] | None = None  # (CoreV1Api, stream_fn)


def _get_clients() -> tuple[Any, Any]:
    """CoreV1Api + exec-stream function. In-cluster config, kubeconfig fallback."""
    global _clients
    if _clients is None:
        from kubernetes import client, config
        from kubernetes.stream import stream

        try:
            config.load_incluster_config()
        except config.ConfigException:
            # Local development / testing against a kubeconfig context.
            config.load_kube_config()
        _clients = (client.CoreV1Api(), stream)
    return _clients


def resolve_namespace() -> str:
    if settings.k8s_sandbox_namespace:
        return settings.k8s_sandbox_namespace
    ns = os.environ.get("POD_NAMESPACE")
    if ns:
        return ns
    try:
        with open("/var/run/secrets/kubernetes.io/serviceaccount/namespace") as f:
            return f.read().strip()
    except OSError:
        return "default"


def _k8s_quantity(docker_size: str) -> str:
    """Docker size string ('500m', '1g') → k8s quantity ('500Mi', '1Gi')."""
    s = docker_size.strip()
    lowered = s.lower()
    if lowered.endswith(("ki", "mi", "gi")):
        return s  # already a k8s quantity
    for suffix, unit in (("g", "Gi"), ("m", "Mi"), ("k", "Ki")):
        if lowered.endswith(suffix):
            return f"{s[:-1]}{unit}"
    return s


def _pod_manifest(name: str, image: str, deadline_seconds: int) -> dict[str, Any]:
    """Hardened single-use pod. Mirrors `_ephemeral_runtime._run_config`."""
    spec: dict[str, Any] = {
        "restartPolicy": "Never",
        "automountServiceAccountToken": False,
        "terminationGracePeriodSeconds": 1,
        # Backstop against leaked pods if the deleting process dies mid-flight.
        "activeDeadlineSeconds": deadline_seconds,
        "containers": [
            {
                "name": "executor",
                "image": image,
                "imagePullPolicy": "IfNotPresent",
                "env": [
                    {"name": "PYTHONUNBUFFERED", "value": "1"},
                    {"name": "SANDBOX_CONTAINER", "value": "true"},
                    {"name": "SINAS_CONTAINER_MODE", "value": "sandbox"},
                ],
                "resources": {
                    "requests": {
                        "memory": f"{settings.max_function_memory}Mi",
                        "cpu": str(settings.max_function_cpu),
                    },
                    "limits": {
                        "memory": f"{settings.max_function_memory}Mi",
                        "cpu": str(settings.max_function_cpu),
                        "ephemeral-storage": _k8s_quantity(
                            settings.max_function_storage
                        ),
                    },
                },
                "securityContext": {
                    "allowPrivilegeEscalation": False,
                    "capabilities": {
                        "drop": ["ALL"],
                        "add": ["CHOWN", "SETUID", "SETGID"],
                    },
                    "seccompProfile": {"type": "RuntimeDefault"},
                },
                "volumeMounts": [{"name": "tmp", "mountPath": "/tmp"}],
            }
        ],
        "volumes": [
            {
                "name": "tmp",
                "emptyDir": {"medium": "Memory", "sizeLimit": "100Mi"},
            }
        ],
    }
    if settings.k8s_sandbox_service_account:
        spec["serviceAccountName"] = settings.k8s_sandbox_service_account

    labels = dict(_POD_LABELS)
    if settings.k8s_release_name:
        # Present so chart-authored affinity rules (either direction) have
        # something to match on — this app doesn't decide spread vs. pack,
        # it just labels the pod and applies whatever scheduling config it's
        # handed (see k8s_sandbox_* settings docs in config.py).
        labels["app.kubernetes.io/instance"] = settings.k8s_release_name

    node_selector = json.loads(settings.k8s_sandbox_node_selector)
    if node_selector:
        spec["nodeSelector"] = node_selector
    tolerations = json.loads(settings.k8s_sandbox_tolerations)
    if tolerations:
        spec["tolerations"] = tolerations
    affinity = json.loads(settings.k8s_sandbox_affinity)
    if affinity:
        spec["affinity"] = affinity

    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": name, "labels": labels},
        "spec": spec,
    }


def _exec_blocking(
    name: str,
    namespace: str,
    command: list[str],
    *,
    stdin_payload: str | None = None,
    timeout: float,
) -> tuple[str, str, int]:
    """Run a command in the pod's executor container; return (stdout, stderr, rc).

    When `stdin_payload` is set, it is length-prefixed (`"<nbytes>\\n" + data`)
    because the exec websocket cannot half-close stdin — the remote reader
    counts bytes instead of waiting for EOF.
    """
    api, stream_fn = _get_clients()
    resp = stream_fn(
        api.connect_get_namespaced_pod_exec,
        name,
        namespace,
        container="executor",
        command=command,
        stdin=stdin_payload is not None,
        stdout=True,
        stderr=True,
        tty=False,
        _preload_content=False,
    )
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    try:
        if stdin_payload is not None:
            nbytes = len(stdin_payload.encode("utf-8"))
            resp.write_stdin(f"{nbytes}\n")
            resp.write_stdin(stdin_payload)
        deadline = time.time() + timeout
        while resp.is_open() and time.time() < deadline:
            resp.update(timeout=1)
            if resp.peek_stdout():
                stdout_parts.append(resp.read_stdout())
            if resp.peek_stderr():
                stderr_parts.append(resp.read_stderr())
        try:
            rc = resp.returncode
        except Exception:
            rc = None
    finally:
        resp.close()
    if rc is None:
        # Stream still open at the deadline (or no status frame): treat as
        # success iff we got stdout — the payload scripts always print.
        rc = 0 if stdout_parts else 124
    return "".join(stdout_parts), "".join(stderr_parts), rc


# Remote reader for the length-prefixed stdin protocol (see _exec_blocking).
_STDIN_WRITER = """
import sys
line = b""
while not line.endswith(b"\\n"):
    c = sys.stdin.buffer.read(1)
    if not c:
        break
    line += c
n = int(line)
data = b""
while len(data) < n:
    chunk = sys.stdin.buffer.read(n - len(data))
    if not chunk:
        break
    data += chunk
with open({path!r}, "wb") as f:
    f.write(data)
print("WROTE", len(data), flush=True)
"""


def _wait_ready_blocking(name: str, namespace: str, timeout: float) -> None:
    api, _ = _get_clients()
    deadline = time.time() + timeout
    last_phase = "Unknown"
    while time.time() < deadline:
        pod = api.read_namespaced_pod(name=name, namespace=namespace)
        last_phase = pod.status.phase or "Unknown"
        if last_phase in ("Failed", "Succeeded"):
            raise RuntimeError(
                f"Sandbox pod {name} entered terminal phase {last_phase} before ready"
            )
        statuses = pod.status.container_statuses or []
        if last_phase == "Running" and statuses and statuses[0].ready:
            return
        time.sleep(0.25)
    raise TimeoutError(
        f"Sandbox pod {name} not ready after {timeout}s (phase={last_phase})"
    )


async def create_sandbox_pod(
    db: AsyncSession, *, execution_id: str, name_prefix: str = "sinas-sbx"
) -> tuple[str, str]:
    """Create a hardened single-use sandbox pod and wait until it is ready.

    Returns (name, namespace). The caller is responsible for
    `delete_sandbox_pod` — including on failure paths after this returns.
    """
    from app.services.sandbox_image import _dependency_specs

    image = settings.k8s_sandbox_image or settings.function_container_image
    namespace = resolve_namespace()
    name = f"{name_prefix}-{execution_id}".lower()

    specs = (
        await _dependency_specs(db)
        if settings.k8s_sandbox_install_dependencies
        else []
    )
    deadline_seconds = (
        settings.k8s_sandbox_pod_ready_timeout
        + (_DEPS_INSTALL_TIMEOUT if specs else 0)
        + settings.function_timeout
        + 60
    )
    manifest = _pod_manifest(name, image, deadline_seconds)

    api, _ = _get_clients()

    def _create() -> None:
        from kubernetes.client.exceptions import ApiException

        try:
            api.create_namespaced_pod(namespace=namespace, body=manifest)
        except ApiException as e:
            if e.status == 409:
                # Stale pod squatting on this name (crashed prior run of the
                # same execution_id) — replace it.
                api.delete_namespaced_pod(
                    name=name, namespace=namespace, grace_period_seconds=0
                )
                for _ in range(40):
                    try:
                        api.read_namespaced_pod(name=name, namespace=namespace)
                        time.sleep(0.25)
                    except ApiException as e2:
                        if e2.status == 404:
                            break
                        raise
                api.create_namespaced_pod(namespace=namespace, body=manifest)
            else:
                raise

    await asyncio.to_thread(_create)
    try:
        await asyncio.to_thread(
            _wait_ready_blocking,
            name,
            namespace,
            settings.k8s_sandbox_pod_ready_timeout,
        )
        if specs:
            stdout, stderr, rc = await asyncio.to_thread(
                _exec_blocking,
                name,
                namespace,
                ["pip", "install", "--no-cache-dir", *specs],
                timeout=_DEPS_INSTALL_TIMEOUT,
            )
            if rc != 0:
                raise RuntimeError(
                    f"Dependency install failed in sandbox pod {name}: "
                    f"{(stderr or stdout)[-2000:]}"
                )
    except Exception:
        await delete_sandbox_pod(name, namespace)
        raise
    return name, namespace


async def delete_sandbox_pod(name: str, namespace: str) -> None:
    api, _ = _get_clients()

    def _delete() -> None:
        from kubernetes.client.exceptions import ApiException

        try:
            api.delete_namespaced_pod(
                name=name, namespace=namespace, grace_period_seconds=0
            )
        except ApiException as e:
            if e.status != 404:
                raise

    try:
        await asyncio.to_thread(_delete)
    except Exception as e:
        logger.warning("Failed to delete sandbox pod %s: %s", name, e)


async def run_payload_in_pod(
    name: str,
    namespace: str,
    payload: dict[str, Any],
    timeout: int,
    fetch_handler=None,
) -> dict[str, Any]:
    """Run one execute_inline payload against the pod's executor daemon.

    Same request/trigger/result-file protocol as the Docker runtimes,
    including the lazy-fetch extension: workbench file requests from the
    wrapper are served through `fetch_handler` and the wait re-entered.
    Returns the parsed wire dict; raises on infra errors (exec failure,
    bad JSON).
    """
    import time as _time

    from app.core.config import settings
    from app.services.executor._wire import (
        build_wait_script,
        fetch_response_path,
        parse_wait_output,
    )

    eid = payload["execution_id"]
    request_path = f"/tmp/exec_request_{eid}.json"

    async def _write_into_pod(path: str, data: str) -> None:
        writer = _STDIN_WRITER.format(path=path)
        stdout, stderr, rc = await asyncio.to_thread(
            _exec_blocking,
            name,
            namespace,
            ["python3", "-c", writer],
            stdin_payload=data,
            timeout=60,
        )
        expected = f"WROTE {len(data.encode('utf-8'))}"
        if expected not in stdout:
            raise RuntimeError(
                f"Failed to write {path} into sandbox pod {name}: "
                f"rc={rc} stdout={stdout[-500:]!r} stderr={stderr[-500:]!r}"
            )

    await _write_into_pod(request_path, json.dumps(payload))

    deadline = _time.time() + timeout
    first_wait = True
    fetches_served = 0
    while True:
        remaining = max(1.0, deadline - _time.time())
        stdout, stderr, rc = await asyncio.to_thread(
            _exec_blocking,
            name,
            namespace,
            ["python3", "-c", build_wait_script(eid, remaining, write_trigger=first_wait)],
            timeout=remaining + 30,
        )
        first_wait = False
        if not stdout.strip():
            raise RuntimeError(
                f"No result from sandbox pod {name}: rc={rc} stderr={stderr[-500:]!r}"
            )
        envelope = parse_wait_output(stdout)
        if envelope is None:
            return json.loads(stdout.strip())

        if envelope["kind"] == "fetch":
            req = envelope.get("req") or {}
            path = req.get("path", "")
            fetches_served += 1
            if fetch_handler is None:
                resp: dict[str, Any] = {"path": path, "error": "lazy fetch is not available"}
            elif fetches_served > settings.workbench_lazy_fetch_max_calls:
                resp = {"path": path, "error": "lazy fetch call limit reached for this execution"}
            else:
                resp = await fetch_handler(path)
            await _write_into_pod(fetch_response_path(eid), json.dumps(resp))
            continue

        return envelope["data"]
