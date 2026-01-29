"""Packages API endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
import uuid

from app.core.database import get_db
from app.core.auth import require_permission, get_current_user, set_permission_used
from app.core.config import settings
from app.models.package import InstalledPackage
from app.schemas import PackageInstall, PackageResponse

router = APIRouter(prefix="/packages", tags=["packages"])


@router.post("", response_model=PackageResponse)
async def install_package(
    request: Request,
    package_data: PackageInstall,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(require_permission("sinas.packages.install:all"))  # Admin only
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
        whitelist = {pkg.strip() for pkg in settings.allowed_packages.split(',')}
        if package_data.package_name not in whitelist:
            raise HTTPException(
                status_code=403,
                detail=f"Package '{package_data.package_name}' not in whitelist. Allowed packages: {', '.join(sorted(whitelist))}"
            )

    # Check if already approved
    result = await db.execute(
        select(InstalledPackage).where(
            InstalledPackage.package_name == package_data.package_name
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"Package '{package_data.package_name}' already approved")

    # Record package approval (actual installation happens in containers)
    package = InstalledPackage(
        package_name=package_data.package_name,
        version=package_data.version,
        installed_by=uuid.UUID(user_id)
    )

    db.add(package)
    await db.commit()
    await db.refresh(package)

    return package


@router.get("", response_model=List[PackageResponse])
async def list_packages(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    """List all approved global packages (visible to all authenticated users)."""
    set_permission_used(request, "sinas.packages.get:own")

    # All packages are global, visible to everyone
    result = await db.execute(select(InstalledPackage))
    packages = result.scalars().all()

    return packages


@router.delete("/{package_id}")
async def remove_package(
    request: Request,
    package_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(require_permission("sinas.packages.delete:all"))  # Admin only
):
    """
    Remove package approval (admin only).

    Note: Existing containers with this package will keep it until recreated.
    New containers won't install it.
    """
    result = await db.execute(
        select(InstalledPackage).where(InstalledPackage.id == package_id)
    )
    package = result.scalar_one_or_none()

    if not package:
        raise HTTPException(status_code=404, detail="Package not found")

    package_name = package.package_name
    await db.delete(package)
    await db.commit()

    return {"message": f"Package '{package_name}' approval removed. Existing containers will keep it until recreated."}
