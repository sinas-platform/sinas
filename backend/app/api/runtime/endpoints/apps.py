"""Runtime apps endpoint — app status validation."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.models.app import App
from app.schemas.app import AppStatusResponse
from app.services.app_validator import validate_app_status

router = APIRouter(prefix="/apps", tags=["runtime-apps"])


@router.get("/{namespace}/{name}/status", response_model=AppStatusResponse)
async def get_app_status(
    namespace: str,
    name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Validate an app's resource dependencies and permission requirements."""
    user_id, permissions = current_user_data

    app = await App.get_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="read",
        namespace=namespace,
        name=name,
    )

    set_permission_used(request, f"sinas.apps/{namespace}/{name}.read")

    return await validate_app_status(db, app, user_id, permissions)
