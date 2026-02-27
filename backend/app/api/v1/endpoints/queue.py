"""Queue introspection endpoints for admin monitoring."""
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth import require_permission
from app.services.queue_service import queue_service

router = APIRouter(prefix="/queue", tags=["queue"])


@router.get("/stats")
async def get_queue_stats(
    user_id: str = Depends(require_permission("sinas.system.read:all")),
) -> dict[str, Any]:
    """Aggregate queue, job, DLQ, pool, and worker stats."""
    stats = await queue_service.get_queue_stats()

    # Add container pool stats
    try:
        from app.services.container_pool import container_pool

        pool_stats = container_pool.get_stats()
        stats["pool"] = {
            "idle": pool_stats.get("idle", 0),
            "in_use": pool_stats.get("in_use", 0),
            "total": pool_stats.get("total", 0),
        }
    except Exception:
        stats["pool"] = {"idle": 0, "in_use": 0, "total": 0}

    # Add shared worker stats
    try:
        from app.services.shared_worker_manager import shared_worker_manager
        workers = await shared_worker_manager.list_workers()
        running = sum(1 for w in workers if w.get("status") == "running")
        stats["workers"] = {"count": len(workers), "running": running}
    except Exception:
        stats["workers"] = {"count": 0, "running": 0}

    return stats


@router.get("/jobs")
async def list_jobs(
    status: Optional[str] = Query(None, description="Filter by status"),
    limit: int = Query(50, ge=1, le=500),
    user_id: str = Depends(require_permission("sinas.system.read:all")),
) -> list[dict[str, Any]]:
    """List jobs with optional status filter."""
    return await queue_service.get_jobs_list(status=status, limit=limit)


@router.get("/dlq")
async def get_dlq(
    user_id: str = Depends(require_permission("sinas.system.read:all")),
) -> list[dict[str, Any]]:
    """List dead-letter queue entries."""
    return await queue_service.get_dlq_entries()


@router.get("/workers")
async def list_queue_workers(
    user_id: str = Depends(require_permission("sinas.system.read:all")),
) -> list[dict[str, Any]]:
    """List active arq worker processes."""
    return await queue_service.get_active_workers()


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(
    job_id: str,
    user_id: str = Depends(require_permission("sinas.system.update:all")),
) -> dict[str, Any]:
    """Cancel a running or queued job."""
    try:
        return await queue_service.cancel_job(job_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/dlq/{job_id}/retry")
async def retry_dlq_job(
    job_id: str,
    user_id: str = Depends(require_permission("sinas.system.update:all")),
) -> dict[str, Any]:
    """Remove a job from the DLQ and re-enqueue it."""
    try:
        return await queue_service.retry_dlq_job(job_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
