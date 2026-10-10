"""Dependencies API endpoints (pip packages for function containers).

Writes go through DependencyApplier, the path config apply and packages use
too: the same ownership and change history on every channel.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user, require_permission, set_permission_used
from app.core.database import get_db
from app.models.dependency import Dependency
from app.schemas.dependency import DependencyInstall, DependencyResponse

from app.services.resources import rest
from app.services.resources.dependencies import DependencyApplier, installation_problem

router = APIRouter(prefix="/dependencies", tags=["dependencies"])

_applier = DependencyApplier()


@router.post("", response_model=DependencyResponse)
async def install_dependency(
    request: Request,
    package_data: DependencyInstall,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(require_permission("sinas.dependencies.install:all")),  # Admin only
):
    """
    Approve a global package for use in functions (admin only).

    This doesn't install the package immediately - packages are installed
    on-demand in containers when functions require them.
    """
    problem = installation_problem(package_data.package_name)
    if problem:
        raise HTTPException(status_code=403, detail=problem)

    ctx = rest.api_context(db, user_id)
    try:
        result = await rest.write(
            _applier, ctx, rest.parse_spec(_applier, package_data.model_dump()), must_create=True
        )
    except HTTPException as e:
        if e.status_code == 400 and "already exists" in str(e.detail):
            raise HTTPException(
                status_code=400, detail=f"Package '{package_data.package_name}' already approved"
            )
        raise
    await rest.commit(db, ctx)
    await db.refresh(result.obj)
    return result.obj


@router.get("", response_model=list[DependencyResponse])
async def list_dependencies(
    request: Request, db: AsyncSession = Depends(get_db), user_id: str = Depends(get_current_user)
):
    """List all approved global packages (visible to all authenticated users)."""
    set_permission_used(request, "sinas.dependencies.read:own")

    # All dependencies are global, visible to everyone
    result = await db.execute(select(Dependency))
    dependencies = result.scalars().all()

    return dependencies


@router.delete("/{dependency_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_dependency(
    request: Request,
    dependency_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(require_permission("sinas.dependencies.delete:all")),  # Admin only
):
    """
    Remove package approval (admin only).

    Note: Existing containers with this package will keep it until recreated.
    New containers won't install it.
    """
    result = await db.execute(select(Dependency).where(Dependency.id == dependency_id))
    dependency = result.scalar_one_or_none()

    if not dependency:
        raise HTTPException(status_code=404, detail="Dependency not found")

    ctx = rest.api_context(db, user_id)
    await _applier.delete(await rest.locked(_applier, ctx, dependency), ctx)
    await rest.commit(db, ctx)

    return None
