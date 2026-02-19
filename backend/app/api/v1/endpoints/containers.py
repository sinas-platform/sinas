"""Container pool management endpoints."""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_permission
from app.core.database import get_db

router = APIRouter(prefix="/containers", tags=["containers"])


class ScaleRequest(BaseModel):
    target: int


@router.get("/stats")
async def get_container_stats(
    user_id: str = Depends(require_permission("sinas.containers.read:all")),
) -> dict[str, Any]:
    """Get pool container stats. Admin only."""
    from app.services.container_pool import container_pool

    return container_pool.get_stats()


@router.post("/reload")
async def reload_pool_packages(
    current_user_id: str = Depends(require_permission("sinas.containers.update:all")),
    db: AsyncSession = Depends(get_db),
):
    """
    Reinstall all approved packages in idle pool containers.
    Admin only.
    """
    from app.services.container_pool import container_pool

    result = await container_pool.reload_packages(db)
    return result


@router.post("/scale")
async def scale_pool(
    body: ScaleRequest,
    current_user_id: str = Depends(require_permission("sinas.containers.update:all")),
    db: AsyncSession = Depends(get_db),
):
    """Scale the container pool to a target size. Admin only."""
    from app.services.container_pool import container_pool

    if body.target < 0:
        raise HTTPException(status_code=400, detail="Target must be non-negative")

    result = await container_pool.scale(body.target, db)
    return result
