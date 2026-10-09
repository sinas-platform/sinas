"""Store management endpoints.

Writes go through StoreApplier, the path config apply and package install
use too: the same validation, ownership and change history on every channel.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models.store import Store
from app.schemas.store import StoreCreate, StoreResponse, StoreUpdate
from app.services.resources import rest
from app.services.resources.stores import StoreApplier

router = APIRouter(prefix="/stores", tags=["stores"])

_applier = StoreApplier()


@router.post("", response_model=StoreResponse, status_code=status.HTTP_201_CREATED)
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

    ctx = rest.api_context(db, user_id)
    # A clash is a 400 "Store 'ns/name' already exists", as before.
    result = await rest.write(
        _applier, ctx, rest.parse_spec(_applier, store_data.model_dump()), must_create=True
    )
    await rest.commit(db, ctx)
    await db.refresh(result.obj)
    return StoreResponse.model_validate(result.obj)


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

    ctx = rest.api_context(db, user_id)
    store = await rest.locked(_applier, ctx, store)
    # As before: a field left out or sent as null stays as it is, and a store
    # is not renamed here (namespace/name in the body were always ignored).
    patch = {
        field: value
        for field, value in store_data.model_dump(exclude_unset=True).items()
        if value is not None and field not in ("namespace", "name")
    }
    await rest.write(_applier, ctx, rest.patch_spec(_applier, store, patch), existing=store)
    await rest.commit(db, ctx)
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

    ctx = rest.api_context(db, user_id)
    await _applier.delete(await rest.locked(_applier, ctx, store), ctx)
    await rest.commit(db, ctx)
    return None
