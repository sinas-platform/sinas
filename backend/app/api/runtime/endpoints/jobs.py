"""Job status endpoints for queued function executions."""
from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.auth import get_current_user_with_permissions
from app.services.queue_service import queue_service

router = APIRouter()


@router.get("/jobs/{job_id}")
async def get_job_status(
    job_id: str,
    request: Request,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Get the status of a queued job."""
    status = await queue_service.get_job_status(job_id)
    if not status:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job_id": job_id, **status}


@router.get("/jobs/{job_id}/result")
async def get_job_result(
    job_id: str,
    request: Request,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Get the result of a completed job."""
    status = await queue_service.get_job_status(job_id)
    if not status:
        raise HTTPException(status_code=404, detail="Job not found")

    if status.get("status") != "completed":
        raise HTTPException(
            status_code=409,
            detail=f"Job is not completed. Current status: {status.get('status')}",
        )

    result = await queue_service.get_job_result(job_id)
    return {"job_id": job_id, "result": result}
