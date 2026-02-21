"""Apps API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models.app import App
from app.schemas.app import AppCreate, AppResponse, AppUpdate

router = APIRouter(prefix="/apps", tags=["apps"])


@router.post("", response_model=AppResponse)
async def create_app(
    request: Request,
    app_data: AppCreate,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Create a new app registration."""
    user_id, permissions = current_user_data

    permission = "sinas.apps.create:own"
    if not check_permission(permissions, permission):
        set_permission_used(request, permission, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to create apps")
    set_permission_used(request, permission)

    # Check if app name already exists in this namespace
    result = await db.execute(
        select(App).where(and_(App.namespace == app_data.namespace, App.name == app_data.name))
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail=f"App '{app_data.namespace}/{app_data.name}' already exists",
        )

    app = App(
        user_id=user_id,
        namespace=app_data.namespace,
        name=app_data.name,
        description=app_data.description,
        required_resources=[r.model_dump() for r in app_data.required_resources],
        required_permissions=app_data.required_permissions,
        optional_permissions=app_data.optional_permissions,
        exposed_namespaces=app_data.exposed_namespaces,
    )

    db.add(app)
    await db.commit()
    await db.refresh(app)

    return AppResponse.model_validate(app)


@router.get("", response_model=list[AppResponse])
async def list_apps(
    request: Request,
    namespace: str = None,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """List all apps accessible to the user."""
    user_id, permissions = current_user_data

    additional_filters = App.is_active == True
    if namespace:
        additional_filters = and_(additional_filters, App.namespace == namespace)

    apps = await App.list_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="read",
        additional_filters=additional_filters,
    )

    set_permission_used(request, "sinas.apps.read")

    return [AppResponse.model_validate(app) for app in apps]


@router.get("/{namespace}/{name}", response_model=AppResponse)
async def get_app(
    namespace: str,
    name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Get a specific app by namespace and name."""
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

    return AppResponse.model_validate(app)


@router.put("/{namespace}/{name}", response_model=AppResponse)
async def update_app(
    namespace: str,
    name: str,
    app_data: AppUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Update an app."""
    user_id, permissions = current_user_data

    app = await App.get_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="update",
        namespace=namespace,
        name=name,
    )

    set_permission_used(request, f"sinas.apps/{namespace}/{name}.update")

    # If namespace or name is being updated, check for conflicts
    new_namespace = app_data.namespace or app.namespace
    new_name = app_data.name or app.name

    if new_namespace != app.namespace or new_name != app.name:
        result = await db.execute(
            select(App).where(
                and_(App.namespace == new_namespace, App.name == new_name, App.id != app.id)
            )
        )
        if result.scalar_one_or_none():
            raise HTTPException(
                status_code=400, detail=f"App '{new_namespace}/{new_name}' already exists"
            )

    if app_data.namespace is not None:
        app.namespace = app_data.namespace
    if app_data.name is not None:
        app.name = app_data.name
    if app_data.description is not None:
        app.description = app_data.description
    if app_data.required_resources is not None:
        app.required_resources = [r.model_dump() for r in app_data.required_resources]
    if app_data.required_permissions is not None:
        app.required_permissions = app_data.required_permissions
    if app_data.optional_permissions is not None:
        app.optional_permissions = app_data.optional_permissions
    if app_data.exposed_namespaces is not None:
        app.exposed_namespaces = app_data.exposed_namespaces
    if app_data.is_active is not None:
        app.is_active = app_data.is_active

    await db.commit()
    await db.refresh(app)

    return AppResponse.model_validate(app)


@router.delete("/{namespace}/{name}", status_code=204)
async def delete_app(
    namespace: str,
    name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Delete an app."""
    user_id, permissions = current_user_data

    app = await App.get_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="delete",
        namespace=namespace,
        name=name,
    )

    set_permission_used(request, f"sinas.apps/{namespace}/{name}.delete")

    await db.delete(app)
    await db.commit()

    return None
