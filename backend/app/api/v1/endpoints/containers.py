"""Container management endpoints."""
from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict, Any

from app.core.auth import require_permission
from app.core.config import settings

router = APIRouter(prefix="/containers", tags=["containers"])


@router.get("/stats")
async def get_container_stats(
    user_id: str = Depends(require_permission("sinas.containers.get:all"))
) -> List[Dict[str, Any]]:
    """Get stats for all user containers. Admin only."""
    if settings.function_execution_mode != 'docker':
        raise HTTPException(
            status_code=400,
            detail="Docker execution mode not enabled"
        )

    from app.services.user_container_manager import container_manager
    stats = await container_manager.get_container_stats()
    return stats


@router.post("/{user_id}/reload")
async def reload_user_functions(
    user_id: str,
    current_user_id: str = Depends(require_permission("sinas.containers.put:all"))
):
    """Reload functions in user's container. Admin only."""
    if settings.function_execution_mode != 'docker':
        raise HTTPException(
            status_code=400,
            detail="Docker execution mode not enabled"
        )

    from app.services.user_container_manager import container_manager
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        await container_manager.reload_functions(user_id, db)

    return {"status": "reloaded", "user_id": user_id}


@router.delete("/{user_id}")
async def stop_user_container(
    user_id: str,
    current_user_id: str = Depends(require_permission("sinas.containers.delete:all"))
):
    """Stop and remove user's container. Admin only."""
    if settings.function_execution_mode != 'docker':
        raise HTTPException(
            status_code=400,
            detail="Docker execution mode not enabled"
        )

    from app.services.user_container_manager import container_manager
    await container_manager.stop_container(user_id)

    return {"status": "stopped", "user_id": user_id}
