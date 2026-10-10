"""Manifests API endpoints.

Writes go through ManifestApplier, the path config apply and package install
use too: the same validation, ownership and change history on every channel.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models.manifest import Manifest
from app.schemas.manifest import ManifestCreate, ManifestResponse, ManifestUpdate
from app.services.resources import rest
from app.services.resources.manifests import ManifestApplier

router = APIRouter(prefix="/manifests", tags=["manifests"])

_applier = ManifestApplier()


@router.post("", response_model=ManifestResponse, status_code=status.HTTP_201_CREATED)
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

    ctx = rest.api_context(db, user_id)
    # A clash is a 400 "Manifest 'ns/name' already exists", as before.
    result = await rest.write(
        _applier, ctx, rest.parse_spec(_applier, manifest_data.model_dump()), must_create=True
    )
    await rest.commit(db, ctx)
    await db.refresh(result.obj)
    return ManifestResponse.model_validate(result.obj)


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

    ctx = rest.api_context(db, user_id)
    manifest = await rest.locked(_applier, ctx, manifest)
    # As before: a field left out or sent as null stays as it is; a new
    # namespace/name renames it (a clash is a 400).
    patch = {
        field: value
        for field, value in manifest_data.model_dump(exclude_unset=True).items()
        if value is not None
    }
    await rest.write(_applier, ctx, rest.patch_spec(_applier, manifest, patch), existing=manifest)
    await rest.commit(db, ctx)
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

    ctx = rest.api_context(db, user_id)
    await _applier.delete(await rest.locked(_applier, ctx, manifest), ctx)
    await rest.commit(db, ctx)
    return None
