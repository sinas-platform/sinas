"""Manifests API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models.manifest import Manifest
from app.schemas.manifest import ManifestCreate, ManifestResponse, ManifestUpdate
from app.services.package_service import detach_if_package_managed

router = APIRouter(prefix="/manifests", tags=["manifests"])


@router.post("", response_model=ManifestResponse)
async def create_manifest(
    request: Request,
    manifest_data: ManifestCreate,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Create a new manifest registration."""
    user_id, permissions = current_user_data

    permission = "sinas.manifests.create:own"
    if not check_permission(permissions, permission):
        set_permission_used(request, permission, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to create manifests")
    set_permission_used(request, permission)

    # Check if manifest name already exists in this namespace
    result = await db.execute(
        select(Manifest).where(and_(Manifest.namespace == manifest_data.namespace, Manifest.name == manifest_data.name))
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail=f"Manifest '{manifest_data.namespace}/{manifest_data.name}' already exists",
        )

    manifest = Manifest(
        user_id=user_id,
        namespace=manifest_data.namespace,
        name=manifest_data.name,
        description=manifest_data.description,
        required_resources=[r.model_dump() for r in manifest_data.required_resources],
        required_permissions=manifest_data.required_permissions,
        optional_permissions=manifest_data.optional_permissions,
        exposed_namespaces=manifest_data.exposed_namespaces,
        store_dependencies=[s.model_dump() for s in manifest_data.store_dependencies],
    )

    db.add(manifest)
    await db.flush()
    await db.refresh(manifest)

    return ManifestResponse.model_validate(manifest)


@router.get("", response_model=list[ManifestResponse])
async def list_manifests(
    request: Request,
    namespace: str = None,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """List all manifests accessible to the user."""
    user_id, permissions = current_user_data

    additional_filters = Manifest.is_active == True
    if namespace:
        additional_filters = and_(additional_filters, Manifest.namespace == namespace)

    manifests = await Manifest.list_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="read",
        additional_filters=additional_filters,
    )

    set_permission_used(request, "sinas.manifests.read")

    return [ManifestResponse.model_validate(manifest) for manifest in manifests]


@router.get("/{namespace}/{name}", response_model=ManifestResponse)
async def get_manifest(
    namespace: str,
    name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Get a specific manifest by namespace and name."""
    user_id, permissions = current_user_data

    manifest = await Manifest.get_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="read",
        namespace=namespace,
        name=name,
    )

    set_permission_used(request, f"sinas.manifests/{namespace}/{name}.read")

    return ManifestResponse.model_validate(manifest)


@router.put("/{namespace}/{name}", response_model=ManifestResponse)
async def update_manifest(
    namespace: str,
    name: str,
    manifest_data: ManifestUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Update a manifest."""
    user_id, permissions = current_user_data

    manifest = await Manifest.get_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="update",
        namespace=namespace,
        name=name,
    )

    set_permission_used(request, f"sinas.manifests/{namespace}/{name}.update")

    detach_if_package_managed(manifest)

    # If namespace or name is being updated, check for conflicts
    new_namespace = manifest_data.namespace or manifest.namespace
    new_name = manifest_data.name or manifest.name

    if new_namespace != manifest.namespace or new_name != manifest.name:
        result = await db.execute(
            select(Manifest).where(
                and_(Manifest.namespace == new_namespace, Manifest.name == new_name, Manifest.id != manifest.id)
            )
        )
        if result.scalar_one_or_none():
            raise HTTPException(
                status_code=400, detail=f"Manifest '{new_namespace}/{new_name}' already exists"
            )

    if manifest_data.namespace is not None:
        manifest.namespace = manifest_data.namespace
    if manifest_data.name is not None:
        manifest.name = manifest_data.name
    if manifest_data.description is not None:
        manifest.description = manifest_data.description
    if manifest_data.required_resources is not None:
        manifest.required_resources = [r.model_dump() for r in manifest_data.required_resources]
    if manifest_data.required_permissions is not None:
        manifest.required_permissions = manifest_data.required_permissions
    if manifest_data.optional_permissions is not None:
        manifest.optional_permissions = manifest_data.optional_permissions
    if manifest_data.exposed_namespaces is not None:
        manifest.exposed_namespaces = manifest_data.exposed_namespaces
    if manifest_data.store_dependencies is not None:
        manifest.store_dependencies = [s.model_dump() for s in manifest_data.store_dependencies]
    if manifest_data.is_active is not None:
        manifest.is_active = manifest_data.is_active

    await db.flush()
    await db.refresh(manifest)

    return ManifestResponse.model_validate(manifest)


@router.delete("/{namespace}/{name}", status_code=204)
async def delete_manifest(
    namespace: str,
    name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Delete a manifest."""
    user_id, permissions = current_user_data

    manifest = await Manifest.get_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="delete",
        namespace=namespace,
        name=name,
    )

    set_permission_used(request, f"sinas.manifests/{namespace}/{name}.delete")

    await db.delete(manifest)
    await db.flush()

    return None
