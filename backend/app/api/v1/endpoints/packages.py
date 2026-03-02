"""Packages API endpoints — installable integration packages."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.schemas.package import (
    PackageCreateRequest,
    PackageInstallRequest,
    PackageListResponse,
    PackagePreviewRequest,
    PackageResponse,
)
from app.services.package_service import PackageService

router = APIRouter(prefix="/packages", tags=["packages"])


@router.post("/install", response_model=dict)
async def install_package(
    request: Request,
    body: PackageInstallRequest,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Install a package from YAML content."""
    user_id, permissions = current_user_data

    if not check_permission(permissions, "sinas.packages.install:all"):
        set_permission_used(request, "sinas.packages.install:all", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to install packages")
    set_permission_used(request, "sinas.packages.install:all", has_perm=True)

    service = PackageService(db)
    try:
        package, apply_result = await service.install(body.source, user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "package": PackageResponse.model_validate(package).model_dump(mode="json"),
        "apply": apply_result.model_dump(mode="json"),
    }


@router.post("/preview")
async def preview_package(
    request: Request,
    body: PackagePreviewRequest,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Preview a package install (dry run)."""
    user_id, permissions = current_user_data

    if not check_permission(permissions, "sinas.packages.install:all"):
        set_permission_used(request, "sinas.packages.install:all", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to preview packages")
    set_permission_used(request, "sinas.packages.install:all", has_perm=True)

    service = PackageService(db)
    try:
        result = await service.preview(body.source, user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return result.model_dump(mode="json")


@router.post("/create")
async def create_package(
    request: Request,
    body: PackageCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Create a package YAML from selected resources."""
    user_id, permissions = current_user_data

    if not check_permission(permissions, "sinas.packages.create:all"):
        set_permission_used(request, "sinas.packages.create:all", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to create packages")
    set_permission_used(request, "sinas.packages.create:all", has_perm=True)

    service = PackageService(db)
    resources = [r.model_dump() for r in body.resources]
    yaml_content = await service.create_from_resources(
        name=body.name,
        version=body.version,
        resources=resources,
        description=body.description,
        author=body.author,
        url=body.url,
    )

    return {"yaml": yaml_content}


@router.get("", response_model=list[PackageListResponse])
async def list_packages(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """List all installed packages."""
    user_id, permissions = current_user_data

    if not check_permission(permissions, "sinas.packages.read:own"):
        set_permission_used(request, "sinas.packages.read:own", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to view packages")
    set_permission_used(request, "sinas.packages.read:own", has_perm=True)

    service = PackageService(db)
    return await service.list_packages()


@router.get("/{name}", response_model=PackageResponse)
async def get_package(
    request: Request,
    name: str,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Get package details."""
    user_id, permissions = current_user_data

    if not check_permission(permissions, "sinas.packages.read:own"):
        set_permission_used(request, "sinas.packages.read:own", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to view packages")
    set_permission_used(request, "sinas.packages.read:own", has_perm=True)

    service = PackageService(db)
    package = await service.get_package(name)
    if not package:
        raise HTTPException(status_code=404, detail=f"Package '{name}' not found")

    return package


@router.delete("/{name}")
async def uninstall_package(
    request: Request,
    name: str,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Uninstall a package and delete all its managed resources."""
    user_id, permissions = current_user_data

    if not check_permission(permissions, "sinas.packages.delete:all"):
        set_permission_used(request, "sinas.packages.delete:all", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to uninstall packages")
    set_permission_used(request, "sinas.packages.delete:all", has_perm=True)

    service = PackageService(db)
    try:
        deleted_counts = await service.uninstall(name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return {
        "message": f"Package '{name}' uninstalled",
        "deleted": deleted_counts,
    }


@router.get("/{name}/export")
async def export_package(
    request: Request,
    name: str,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Export a package's YAML."""
    user_id, permissions = current_user_data

    if not check_permission(permissions, "sinas.packages.read:own"):
        set_permission_used(request, "sinas.packages.read:own", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to export packages")
    set_permission_used(request, "sinas.packages.read:own", has_perm=True)

    service = PackageService(db)
    try:
        yaml_content = await service.export_package(name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return PlainTextResponse(yaml_content, media_type="text/yaml")
