"""Runtime manifests endpoint — manifest status validation."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.models.manifest import Manifest
from app.schemas.manifest import ManifestStatusResponse
from app.services.manifest_validator import validate_manifest_status

router = APIRouter(prefix="/manifests", tags=["runtime-manifests"])


@router.get("/{namespace}/{name}/status", response_model=ManifestStatusResponse)
async def get_manifest_status(
    namespace: str,
    name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Validate a manifest's resource dependencies and permission requirements."""
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

    return await validate_manifest_status(db, manifest, user_id, permissions)
