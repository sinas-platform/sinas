"""Store management endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models.store import Store
from app.schemas.store import StoreCreate, StoreResponse, StoreUpdate
from app.services.package_service import detach_if_package_managed

router = APIRouter(prefix="/stores", tags=["stores"])


@router.post("", response_model=StoreResponse)
async def create_store(
    request: Request,
    store_data: StoreCreate,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Create a new store definition."""
    user_id, permissions = current_user_data

    permission = f"sinas.stores/{store_data.namespace}/*.create:own"
    if not check_permission(permissions, permission):
        set_permission_used(request, permission, has_perm=False)
        raise HTTPException(
            status_code=403,
            detail=f"Not authorized to create stores in namespace '{store_data.namespace}'"
        )
    set_permission_used(request, permission)

    # Check uniqueness
    result = await db.execute(
        select(Store).where(
            and_(
                Store.namespace == store_data.namespace,
                Store.name == store_data.name
            )
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail=f"Store '{store_data.namespace}/{store_data.name}' already exists",
        )

    store = Store(
        user_id=user_id,
        namespace=store_data.namespace,
        name=store_data.name,
        description=store_data.description,
        schema=store_data.schema or {},
        strict=store_data.strict,
        default_visibility=store_data.default_visibility,
        encrypted=store_data.encrypted,
    )

    db.add(store)
    await db.commit()
    await db.refresh(store)

    return StoreResponse.model_validate(store)


@router.get("", response_model=list[StoreResponse])
async def list_stores(
    request: Request,
    namespace: str = None,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """List all stores accessible to the user."""
    user_id, permissions = current_user_data

    additional_filters = None
    if namespace:
        additional_filters = Store.namespace == namespace

    stores = await Store.list_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="read",
        additional_filters=additional_filters,
    )

    set_permission_used(request, "sinas.stores.read")

    return [StoreResponse.model_validate(s) for s in stores]


@router.get("/{namespace}/{name}", response_model=StoreResponse)
async def get_store(
    namespace: str,
    name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Get a specific store by namespace and name."""
    user_id, permissions = current_user_data

    store = await Store.get_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="read",
        namespace=namespace,
        name=name,
    )

    set_permission_used(request, f"sinas.stores/{namespace}/{name}.read")

    return StoreResponse.model_validate(store)


@router.put("/{namespace}/{name}", response_model=StoreResponse)
async def update_store(
    namespace: str,
    name: str,
    store_data: StoreUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Update a store's configuration."""
    user_id, permissions = current_user_data

    store = await Store.get_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="update",
        namespace=namespace,
        name=name,
    )

    set_permission_used(request, f"sinas.stores/{namespace}/{name}.update")

    detach_if_package_managed(store)

    if store_data.description is not None:
        store.description = store_data.description
    if store_data.schema is not None:
        store.schema = store_data.schema
    if store_data.strict is not None:
        store.strict = store_data.strict
    if store_data.default_visibility is not None:
        store.default_visibility = store_data.default_visibility
    if store_data.encrypted is not None:
        store.encrypted = store_data.encrypted

    await db.commit()
    await db.refresh(store)

    return StoreResponse.model_validate(store)


@router.delete("/{namespace}/{name}", status_code=204)
async def delete_store(
    namespace: str,
    name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Delete a store and all its states."""
    user_id, permissions = current_user_data

    store = await Store.get_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="delete",
        namespace=namespace,
        name=name,
    )

    set_permission_used(request, f"sinas.stores/{namespace}/{name}.delete")

    await db.delete(store)
    await db.commit()

    return None
