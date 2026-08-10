"""TrustedExecutor: warm, long-lived Kubernetes pods for shared-pool code.

The k8s-native sibling of `DockerSharedTrustedExecutor`. Trusted
(admin-approved, `Function.shared_pool=True`) code runs in a small fleet of
persistent pods created from the executor image in shared-worker mode, and
work is dispatched over the same request/trigger/result-file exec IPC the
k8s sandbox pods use — so per-call latency is an exec round-trip against a
warm pod, not a pod cold-start.

Meter integrity: these pods receive no environment beyond WORKER_MODE /
WORKER_ID (no REDIS_URL, no DATABASE_URL, no ENCRYPTION_KEY), and
`automountServiceAccountToken: false`, so trusted code cannot reach the
usage meter — the property `TRUSTED_EXECUTOR=inprocess` cannot provide.
The chart pairs this with a NetworkPolicy (label
`sinas.type=trusted-executor`) allowing egress to the internet and the
backend API but not to Postgres/Redis/ClickHouse.

Pods have deterministic names ({prefix}-trusted-worker-N), so every platform
process can dispatch without shared discovery state, and a missing or dead
pod is recreated on demand by whoever notices first (create conflicts are
benign). The scheduler pre-warms the fleet at startup.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import replace
from itertools import count
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.executor.base import ExecutionResult, ResultStatus

logger = logging.getLogger(__name__)

_POD_LABELS = {"sinas.type": "trusted-executor"}
_DEPS_INSTALL_TIMEOUT = 300

# Process-wide round-robin cursor; purely a load-spreading hint.
_rr = count()


def _pod_names() -> list[str]:
    prefix = settings.k8s_release_name or "sinas"
    n = max(1, settings.k8s_trusted_workers)
    return [f"{prefix}-trusted-worker-{i}" for i in range(n)]


def _manifest(name: str, image: str, worker_id: str) -> dict[str, Any]:
    """Long-lived trusted worker pod.

    Same hardening as the sandbox manifest (non-root-ish, caps dropped,
    seccomp, no SA token) minus the parts that only make sense for
    single-use pods (activeDeadlineSeconds, restartPolicy=Never). Resources
    mirror the Docker shared workers (1Gi / 1 CPU).
    """
    from app.services.executor._k8s_runtime import _k8s_quantity

    spec: dict[str, Any] = {
        "restartPolicy": "Always",
        "automountServiceAccountToken": False,
        "containers": [
            {
                "name": "executor",
                "image": image,
                "imagePullPolicy": "IfNotPresent",
                "env": [
                    {"name": "PYTHONUNBUFFERED", "value": "1"},
                    {"name": "WORKER_MODE", "value": "true"},
                    {"name": "WORKER_ID", "value": worker_id},
                    {"name": "SINAS_CONTAINER_MODE", "value": "shared"},
                ],
                "resources": {
                    "requests": {"memory": "512Mi", "cpu": "250m"},
                    "limits": {
                        "memory": "1Gi",
                        "cpu": "1",
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
            {"name": "tmp", "emptyDir": {"medium": "Memory", "sizeLimit": "100Mi"}}
        ],
    }
    if settings.k8s_sandbox_service_account:
        # The credential-less sandbox SA; automount is off regardless.
        spec["serviceAccountName"] = settings.k8s_sandbox_service_account

    labels = dict(_POD_LABELS)
    if settings.k8s_release_name:
        labels["app.kubernetes.io/instance"] = settings.k8s_release_name

    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": name, "labels": labels},
        "spec": spec,
    }


def _pod_ready_blocking(name: str, namespace: str) -> bool:
    from kubernetes.client.exceptions import ApiException

    from app.services.executor._k8s_runtime import _get_clients

    api, _ = _get_clients()
    try:
        pod = api.read_namespaced_pod(name=name, namespace=namespace)
    except ApiException as e:
        if e.status == 404:
            return False
        raise
    statuses = pod.status.container_statuses or []
    return pod.status.phase == "Running" and bool(statuses) and statuses[0].ready


async def _ensure_pod(db: AsyncSession | None, name: str, namespace: str) -> None:
    """Create (or replace a dead) trusted worker pod and wait until ready."""
    from kubernetes.client.exceptions import ApiException

    from app.services.executor._k8s_runtime import (
        _exec_blocking,
        _get_clients,
        _wait_ready_blocking,
    )

    api, _ = _get_clients()
    image = settings.k8s_sandbox_image or settings.function_container_image
    worker_id = name.rsplit("-", 1)[-1]
    manifest = _manifest(name, image, worker_id)

    def _create() -> bool:
        """Returns True if we created the pod (vs. found a live one)."""
        try:
            pod = api.read_namespaced_pod(name=name, namespace=namespace)
            if pod.status.phase in ("Failed", "Succeeded"):
                api.delete_namespaced_pod(
                    name=name, namespace=namespace, grace_period_seconds=0
                )
                for _ in range(40):
                    try:
                        api.read_namespaced_pod(name=name, namespace=namespace)
                        time.sleep(0.25)
                    except ApiException as e:
                        if e.status == 404:
                            break
                        raise
            else:
                return False  # exists and is pending/running
        except ApiException as e:
            if e.status != 404:
                raise

        try:
            api.create_namespaced_pod(namespace=namespace, body=manifest)
            return True
        except ApiException as e:
            if e.status == 409:
                return False  # another process won the race — fine
            raise

    created = await asyncio.to_thread(_create)
    await asyncio.to_thread(
        _wait_ready_blocking, name, namespace, settings.k8s_sandbox_pod_ready_timeout
    )

    if created and settings.k8s_sandbox_install_dependencies and db is not None:
        from app.services.sandbox_image import _dependency_specs

        specs = await _dependency_specs(db)
        if specs:
            stdout, stderr, rc = await asyncio.to_thread(
                _exec_blocking,
                name,
                namespace,
                ["pip", "install", "--no-cache-dir", *specs],
                timeout=_DEPS_INSTALL_TIMEOUT,
            )
            if rc != 0:
                logger.warning(
                    "Dependency install failed in trusted pod %s: %s",
                    name,
                    (stderr or stdout)[-1000:],
                )
    if created:
        logger.info("Created trusted worker pod %s", name)


async def ensure_trusted_pool(db: AsyncSession | None = None) -> None:
    """Pre-warm the whole fleet. Called from the scheduler at startup;
    dispatch also calls _ensure_pod lazily for self-healing."""
    from app.services.executor._k8s_runtime import resolve_namespace

    namespace = resolve_namespace()
    for name in _pod_names():
        try:
            await _ensure_pod(db, name, namespace)
        except Exception as e:
            logger.error("Failed to ensure trusted worker pod %s: %s", name, e)


class K8sSharedTrustedExecutor:
    """Warm shared k8s pods. Implements TrustedExecutor."""

    async def execute(
        self,
        *,
        user_id: str,
        user_email: str,
        access_token: str,
        function_namespace: str,
        function_name: str,
        input_data: dict[str, Any],
        execution_id: str,
        trigger_type: str,
        chat_id: str | None,
        user_custom_fields: dict[str, Any] | None = None,
        db: AsyncSession,
        timeout: int,
    ) -> ExecutionResult:
        from app.models.function import Function
        from app.services.executor._k8s_runtime import (
            resolve_namespace,
            run_payload_in_pod,
        )
        from app.services.shared_worker_manager import shared_worker_manager

        result = await db.execute(
            select(Function).where(
                Function.namespace == function_namespace,
                Function.name == function_name,
                Function.is_active == True,
                Function.shared_pool == True,
            )
        )
        function = result.scalar_one_or_none()
        if not function:
            return ExecutionResult.failed(
                f"Function {function_namespace}/{function_name} not found "
                f"or not marked as shared_pool"
            )

        effective_timeout = timeout or settings.function_timeout
        payload = {
            "action": "execute_inline",
            "function_code": function.code,
            "execution_id": execution_id,
            "function_namespace": function_namespace,
            "function_name": function_name,
            "timeout": effective_timeout,
            "input_data": input_data,
            "context": {
                "user_id": user_id,
                "user_email": user_email,
                "user_custom_fields": user_custom_fields or {},
                "access_token": access_token,
                "execution_id": execution_id,
                "trigger_type": trigger_type,
                "chat_id": chat_id,
                # Same trusted-context secrets the Docker shared workers get
                "secrets": await shared_worker_manager._load_secrets(db, user_id),
            },
        }

        namespace = resolve_namespace()
        names = _pod_names()
        start = next(_rr)
        last_error: Exception | None = None
        for i in range(len(names)):
            name = names[(start + i) % len(names)]
            try:
                if not await asyncio.to_thread(_pod_ready_blocking, name, namespace):
                    await _ensure_pod(db, name, namespace)
                wire = await run_payload_in_pod(
                    name, namespace, payload, effective_timeout
                )
                res = ExecutionResult.from_wire(wire)
                if res.status is ResultStatus.AWAITING_INPUT and not res.handle:
                    # The pod doesn't know its own name; stamp the handle so
                    # resume can find the same pod.
                    res = replace(res, handle=name)
                return res
            except Exception as e:
                # Infra failure on this pod (exec error, recreate failed) —
                # try the next one before giving up.
                last_error = e
                logger.warning(
                    "Trusted execution %s failed on pod %s: %s",
                    execution_id,
                    name,
                    e,
                )
        return ExecutionResult.failed(
            f"All trusted worker pods failed: {last_error}"
        )

    async def resume(
        self,
        *,
        handle: str,
        resume_value: Any,
        execution_id: str,
        timeout: int,
    ) -> ExecutionResult:
        """Resume a function parked on input() in trusted pod `handle`.

        k8s twin of `_docker_resume.docker_resume`: write the resume value
        into the pod, then poll for the result the parked thread produces.
        """
        from app.services.executor._k8s_runtime import (
            _STDIN_WRITER,
            _exec_blocking,
            resolve_namespace,
        )

        namespace = resolve_namespace()
        if not await asyncio.to_thread(_pod_ready_blocking, handle, namespace):
            # The parked interpreter state died with the pod.
            return ExecutionResult.failed(
                f"Trusted worker pod {handle} no longer available (restarted?)"
            )

        resume_file = f"/tmp/exec_resume_{execution_id}.json"
        result_file = f"/tmp/exec_result_{execution_id}.json"
        resume_json = json.dumps({"value": resume_value})

        writer = _STDIN_WRITER.format(path=resume_file)
        stdout, stderr, rc = await asyncio.to_thread(
            _exec_blocking,
            handle,
            namespace,
            ["python3", "-c", writer],
            stdin_payload=resume_json,
            timeout=60,
        )
        expected = f"WROTE {len(resume_json.encode('utf-8'))}"
        if expected not in stdout:
            return ExecutionResult.failed(
                f"Failed to write resume value into pod {handle}: "
                f"rc={rc} stderr={stderr[-500:]!r}"
            )

        poll_script = f"""
import sys, json, time, os
max_wait = {timeout}
start = time.time()
while time.time() - start < max_wait:
    if os.path.exists("{result_file}"):
        with open("{result_file}", "r") as f:
            data = json.load(f)
        print(json.dumps(data))
        sys.exit(0)
    time.sleep(0.1)
print(json.dumps({{"status": "failed", "error": "Resume timeout after {timeout}s"}}))
sys.exit(1)
"""
        stdout, stderr, rc = await asyncio.to_thread(
            _exec_blocking,
            handle,
            namespace,
            ["python3", "-c", poll_script],
            timeout=timeout + 30,
        )
        if not stdout.strip():
            return ExecutionResult.failed(
                f"No resume result from pod {handle}: rc={rc} stderr={stderr[-500:]!r}"
            )
        res = ExecutionResult.from_wire(json.loads(stdout.strip()))
        if res.status is ResultStatus.AWAITING_INPUT and not res.handle:
            res = replace(res, handle=handle)
        return res
