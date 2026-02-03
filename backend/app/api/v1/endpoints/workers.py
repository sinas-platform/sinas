"""Workers API endpoints for managing shared worker pool."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.services.shared_worker_manager import shared_worker_manager

router = APIRouter(prefix="/workers", tags=["workers"])


class WorkerResponse(BaseModel):
    """Worker information response."""

    id: str
    container_name: str
    status: str
    created_at: str
    executions: int


class ScaleWorkersRequest(BaseModel):
    """Request to scale workers."""

    target_count: int


class ScaleWorkersResponse(BaseModel):
    """Response from scaling workers."""

    action: str
    previous_count: int
    current_count: int
    added: int = 0
    removed: int = 0


@router.get("", response_model=list[WorkerResponse])
async def list_workers(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """
    List all shared workers.

    Requires sinas.workers.read:all permission (admin only).
    """
    user_id, permissions = current_user_data

    # Check permission
    permission = "sinas.workers.read:all"
    if not check_permission(permissions, permission):
        set_permission_used(request, permission, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to view workers")

    set_permission_used(request, permission)

    # Get workers from manager
    workers = shared_worker_manager.list_workers()

    return workers


@router.post("/scale", response_model=ScaleWorkersResponse)
async def scale_workers(
    request: Request,
    scale_request: ScaleWorkersRequest,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """
    Scale workers up or down to target count.

    Requires sinas.workers.scale:all permission (admin only).
    """
    user_id, permissions = current_user_data

    # Check permission
    permission = "sinas.workers.scale:all"
    if not check_permission(permissions, permission):
        set_permission_used(request, permission, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to scale workers")

    set_permission_used(request, permission)

    # Validate target count
    if scale_request.target_count < 0:
        raise HTTPException(status_code=400, detail="Target count must be >= 0")

    if scale_request.target_count > 10:
        raise HTTPException(status_code=400, detail="Maximum 10 workers allowed")

    # Scale workers
    result = await shared_worker_manager.scale_workers(scale_request.target_count, db)

    return result


@router.get("/count")
async def get_worker_count(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Get current worker count."""
    user_id, permissions = current_user_data

    # Check permission
    permission = "sinas.workers.read:all"
    if not check_permission(permissions, permission):
        set_permission_used(request, permission, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to view workers")

    set_permission_used(request, permission)

    count = shared_worker_manager.get_worker_count()

    return {"count": count}


@router.post("/reload")
async def reload_worker_packages(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """
    Reload packages in all shared workers.
    Reinstalls all approved packages in each worker.

    Requires sinas.workers.put:all permission (admin only).
    """
    user_id, permissions = current_user_data

    # Check permission
    permission = "sinas.workers.put:all"
    if not check_permission(permissions, permission):
        set_permission_used(request, permission, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to reload workers")

    set_permission_used(request, permission)

    # Reload packages
    result = await shared_worker_manager.reload_packages(db)

    return result
