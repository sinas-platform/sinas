from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
import subprocess
import sys
from datetime import datetime

from app.core.database import get_db
from app.models.package import InstalledPackage
from app.api.schemas import PackageInstall, PackageResponse
from app.core.config import settings

router = APIRouter(prefix="/packages", tags=["packages"])


@router.get("", response_model=List[PackageResponse])
async def list_packages(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(InstalledPackage).order_by(InstalledPackage.package_name)
    )
    packages = result.scalars().all()
    
    return packages


@router.post("", response_model=PackageResponse)
async def install_package(
    package_data: PackageInstall,
    db: AsyncSession = Depends(get_db)
):
    if not settings.allow_package_installation:
        raise HTTPException(
            status_code=403, 
            detail="Package installation is disabled"
        )
    
    # Check if package already installed
    existing = await db.execute(
        select(InstalledPackage).where(
            InstalledPackage.package_name == package_data.package_name
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=400, 
            detail="Package already installed"
        )
    
    # Install package using pip
    try:
        package_spec = package_data.package_name
        if package_data.version:
            package_spec += f"=={package_data.version}"
        
        # Run pip install
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", package_spec],
            capture_output=True,
            text=True,
            check=True
        )
        
        # Get installed version
        version_result = subprocess.run(
            [sys.executable, "-m", "pip", "show", package_data.package_name],
            capture_output=True,
            text=True
        )
        
        installed_version = None
        if version_result.returncode == 0:
            for line in version_result.stdout.split('\n'):
                if line.startswith('Version:'):
                    installed_version = line.split(':', 1)[1].strip()
                    break
        
        # Record in database
        package = InstalledPackage(
            package_name=package_data.package_name,
            version=installed_version,
            installed_at=datetime.utcnow(),
            installed_by="system"  # TODO: Add user authentication
        )
        
        db.add(package)
        await db.commit()
        await db.refresh(package)
        
        return package
        
    except subprocess.CalledProcessError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Package installation failed: {e.stderr}"
        )


@router.delete("/{package_name}")
async def uninstall_package(package_name: str, db: AsyncSession = Depends(get_db)):
    if not settings.allow_package_installation:
        raise HTTPException(
            status_code=403, 
            detail="Package management is disabled"
        )
    
    # Check if package exists in our records
    result = await db.execute(
        select(InstalledPackage).where(
            InstalledPackage.package_name == package_name
        )
    )
    package = result.scalar_one_or_none()
    
    if not package:
        raise HTTPException(
            status_code=404, 
            detail="Package not found in installation records"
        )
    
    try:
        # Uninstall package using pip
        subprocess.run(
            [sys.executable, "-m", "pip", "uninstall", package_name, "-y"],
            capture_output=True,
            text=True,
            check=True
        )
        
        # Remove from database
        await db.delete(package)
        await db.commit()
        
        return {"message": f"Package '{package_name}' uninstalled successfully"}
        
    except subprocess.CalledProcessError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Package uninstallation failed: {e.stderr}"
        )