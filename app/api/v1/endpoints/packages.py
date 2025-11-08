"""Packages API endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
import uuid
import subprocess
import sys

from app.core.database import get_db
from app.core.auth import require_permission, get_current_user, get_current_user_with_permissions, set_permission_used
from app.core.config import settings
from app.models.package import InstalledPackage
from app.schemas import PackageInstall, PackageResponse

router = APIRouter(prefix="/packages", tags=["packages"])


@router.post("", response_model=PackageResponse)
async def install_package(
    package_data: PackageInstall,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(require_permission("sinas.packages.install:own"))
):
    """Install a Python package."""
    if not settings.allow_package_installation:
        raise HTTPException(status_code=403, detail="Package installation is disabled")


    # Check if already installed
    result = await db.execute(
        select(InstalledPackage).where(
            InstalledPackage.user_id == user_id,
            InstalledPackage.package_name == package_data.package_name
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"Package '{package_data.package_name}' already installed")

    # Install package
    package_spec = package_data.package_name
    if package_data.version:
        package_spec = f"{package_data.package_name}=={package_data.version}"

    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", package_spec],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE
        )
    except subprocess.CalledProcessError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to install package: {e.stderr.decode() if e.stderr else str(e)}"
        )

    # Record installation
    package = InstalledPackage(
        user_id=user_id,
        package_name=package_data.package_name,
        version=package_data.version,
        installed_by=current_user.get("email")
    )

    db.add(package)
    await db.commit()
    await db.refresh(package)

    return package


@router.get("", response_model=List[PackageResponse])
async def list_packages(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """List installed packages."""
    user_id, permissions = current_user_data

    # Build query based on permissions
    if permissions.get("sinas.packages.read:all"):
        set_permission_used(request, "sinas.packages.read:all")
        query = select(InstalledPackage)
    else:
        set_permission_used(request, "sinas.packages.read:own")
        query = select(InstalledPackage).where(InstalledPackage.user_id == uuid.UUID(user_id))

    result = await db.execute(query)
    packages = result.scalars().all()

    return packages


@router.delete("/{package_id}")
async def uninstall_package(
    request: Request,
    package_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Uninstall a Python package."""
    user_id, permissions = current_user_data

    result = await db.execute(
        select(InstalledPackage).where(InstalledPackage.id == package_id)
    )
    package = result.scalar_one_or_none()

    if not package:
        raise HTTPException(status_code=404, detail="Package not found")

    # Check permissions
    if permissions.get("sinas.packages.delete:all"):
        set_permission_used(request, "sinas.packages.delete:all")
    else:
        if str(package.user_id) != user_id:
            set_permission_used(request, "sinas.packages.delete:own", has_perm=False)
            raise HTTPException(status_code=403, detail="Not authorized to uninstall this package")
        set_permission_used(request, "sinas.packages.delete:own")

    # Uninstall package
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "uninstall", "-y", package.package_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE
        )
    except subprocess.CalledProcessError:
        # Continue even if uninstall fails (package might already be uninstalled)
        pass

    await db.delete(package)
    await db.commit()

    return {"message": f"Package '{package.package_name}' uninstalled successfully"}
