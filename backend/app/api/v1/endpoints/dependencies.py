"""Dependencies API endpoints (pip packages for function containers)."""
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user, require_permission, set_permission_used
from app.core.config import settings
from app.core.database import get_db
from app.models.dependency import Dependency
from app.schemas.dependency import DependencyInstall, DependencyResponse

router = APIRouter(prefix="/dependencies", tags=["dependencies"])


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
    if not settings.allow_package_installation:
        raise HTTPException(status_code=403, detail="Package installation is disabled")

    # Check whitelist if configured
    if settings.allowed_packages:
        whitelist = {pkg.strip() for pkg in settings.allowed_packages.split(",")}
        if package_data.package_name not in whitelist:
            raise HTTPException(
                status_code=403,
                detail=f"Package '{package_data.package_name}' not in whitelist. Allowed packages: {', '.join(sorted(whitelist))}",
            )

    # Check if already approved
    result = await db.execute(
        select(Dependency).where(Dependency.package_name == package_data.package_name)
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=400, detail=f"Package '{package_data.package_name}' already approved"
        )

    # Record package approval (actual installation happens in containers)
    dependency = Dependency(
        package_name=package_data.package_name,
        version=package_data.version,
        installed_at=datetime.now(UTC),
        installed_by=uuid.UUID(user_id),
    )

    db.add(dependency)
    await db.commit()
    await db.refresh(dependency)

    return dependency


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


@router.delete("/{dependency_id}")
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

    package_name = dependency.package_name
    await db.delete(dependency)
    await db.commit()

    return {
        "message": f"Package '{package_name}' approval removed. Existing containers will keep it until recreated."
    }
