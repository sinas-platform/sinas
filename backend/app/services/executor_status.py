"""Executor-aware data for the System page's sandbox / shared container cards.

The cards were written for the Docker executors: a warm sandbox pool and
`sinas-shared-N` containers, both discovered through the Docker socket. The
other executors either have nothing to scale (single-use containers or pods,
in-process trusted code) or keep their pods in Kubernetes. This picks the
right source per mode, and says why when there is nothing to show, instead of
reporting an empty pool.

Every result carries `executor`, `deployment`, `scalable`, `note` (what the
card is showing) and `unavailable_reason` (why it can't show anything).
When that reason is the Kubernetes API (unreachable, not allowed, backing
off), `cluster_unavailable` is true and `retry_after_seconds` says when the
backend tries it again (the console polls no faster).
"""

from __future__ import annotations

import logging
import math
from typing import Any

from app.core.config import settings
from app.services.k8s_stats import (
    SANDBOX_POD_TYPE,
    TRUSTED_POD_TYPE,
    K8sUnavailableError,
    deployment_mode,
    list_executor_pods,
)

logger = logging.getLogger(__name__)

# k8s_stats status words for pods in phase Pending or Running.
_ACTIVE_POD_STATUSES = {"pending", "running", "restarting"}

_CODE_EXECUTION_OFF = "Code execution is disabled (CODE_EXECUTION_ENABLED=false)."


def _meta(executor: str, **kw: Any) -> dict[str, Any]:
    return {
        "executor": executor,
        "deployment": deployment_mode(),
        "scalable": False,
        "note": None,
        "unavailable_reason": None,
        "cluster_unavailable": False,
        "retry_after_seconds": None,
        **kw,
    }


def _cluster_unavailable(e: K8sUnavailableError) -> dict[str, Any]:
    return {
        "unavailable_reason": str(e),
        "cluster_unavailable": True,
        "retry_after_seconds": math.ceil(e.retry_after) if e.retry_after is not None else None,
    }


def _empty_pool() -> dict[str, Any]:
    return {
        "idle": 0,
        "in_use": 0,
        "total": 0,
        "max_size": None,
        "min_idle": None,
        "max_executions": None,
        "idle_containers": [],
        "in_use_containers": [],
    }


async def sandbox_stats() -> dict[str, Any]:
    """Sandbox card: the Docker pool, or in-flight k8s sandbox pods."""
    from app.services.container_pool import container_pool

    mode = settings.sandbox_executor
    if not settings.code_execution_enabled:
        return {**_empty_pool(), **_meta(mode, unavailable_reason=_CODE_EXECUTION_OFF)}

    if mode == "docker_pool":
        return {**container_pool.get_stats(), **_meta(mode, scalable=True)}

    if mode == "docker_ephemeral":
        return {
            **container_pool.get_stats(),
            **_meta(
                mode,
                note="Sandbox code runs in a single-use container per execution "
                "(docker_ephemeral); there is no warm pool to scale.",
            ),
        }

    if mode == "k8s_pod":
        meta = _meta(
            mode,
            note="Sandbox code runs in a single-use pod per execution (k8s_pod); "
            "there is no warm pool to scale. Listed pods are executions in flight.",
        )
        try:
            pods = await list_executor_pods(SANDBOX_POD_TYPE)
        except K8sUnavailableError as e:
            return {**_empty_pool(), **meta, **_cluster_unavailable(e)}
        # Only Pending/Running pods are executions in flight. A pod stopped by
        # activeDeadlineSeconds (or whose deleter died) lingers as
        # completed/failed until someone deletes it; count it (and pods
        # already terminating) separately. Phase Unknown (its node stopped
        # reporting) is neither: the execution may still be running, so the
        # pod stays listed and is counted on its own.
        active = [p for p in pods if p["status"] in _ACTIVE_POD_STATUSES]
        unknown = [p for p in pods if p["status"] == "unknown"]
        finished = len(pods) - len(active) - len(unknown)
        in_use = [
            {
                "name": p["name"],
                "status": p["status"],
                "executions": None,
                "age_seconds": p["age_seconds"],
            }
            for p in active + unknown
        ]
        if unknown:
            meta["note"] += (
                f" {len(unknown)} sandbox pod{'s' if len(unknown) != 1 else ''} in an unknown "
                "state (its node is not reporting): listed, not counted."
            )
        if finished:
            meta["note"] += (
                f" {finished} finished sandbox pod{'s' if finished != 1 else ''} not yet "
                "cleaned up (not counted)."
            )
        return {
            **_empty_pool(),
            "in_use": len(active),
            "total": len(active),
            "finished": finished,
            "unknown": len(unknown),
            "max_executions": 1,
            "in_use_containers": in_use,
            **meta,
        }

    return {
        **_empty_pool(),
        **_meta(
            mode, unavailable_reason="Sandbox execution is disabled (SANDBOX_EXECUTOR=disabled)."
        ),
    }


async def trusted_workers() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Shared card: (workers in WorkerResponse shape, meta)."""
    mode = settings.trusted_executor
    if not settings.code_execution_enabled:
        return [], _meta(mode, unavailable_reason=_CODE_EXECUTION_OFF)

    if mode == "docker_shared":
        from app.services.shared_worker_manager import shared_worker_manager

        return await shared_worker_manager.list_workers(), _meta(mode, scalable=True)

    if mode == "k8s_shared":
        meta = _meta(
            mode,
            note="Warm trusted pods (k8s_shared), sized by executor.trustedWorkers "
            f"in the Helm chart (currently {settings.k8s_trusted_workers}); they "
            "can't be scaled from the console.",
        )
        try:
            pods = await list_executor_pods(TRUSTED_POD_TYPE)
        except K8sUnavailableError as e:
            return [], {**meta, **_cluster_unavailable(e)}
        workers = [
            {
                "id": f"worker-{p['name'].rsplit('-', 1)[-1]}",
                "container_name": p["name"],
                "status": p["status"],
                "created_at": p["created_at"],
                # Per-worker execution counts are only kept by the Docker pool.
                "executions": 0,
            }
            for p in pods
        ]
        return workers, meta

    if mode == "inprocess":
        return [], _meta(
            mode,
            note="Trusted code runs inside the queue-worker processes "
            "(TRUSTED_EXECUTOR=inprocess); there are no shared containers.",
        )

    return [], _meta(
        mode, unavailable_reason="Shared-pool execution is disabled (TRUSTED_EXECUTOR=disabled)."
    )


async def trusted_worker_count() -> dict[str, Any]:
    """Shared card header: {count, **meta}."""
    if settings.code_execution_enabled and settings.trusted_executor == "docker_shared":
        from app.services.shared_worker_manager import shared_worker_manager

        return {
            "count": shared_worker_manager.get_worker_count(),
            **_meta("docker_shared", scalable=True),
        }
    workers, meta = await trusted_workers()
    return {"count": len(workers), **meta}
