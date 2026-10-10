"""Collection management endpoints.

Writes go through CollectionApplier, the path config apply and package
install use too: the same validation, ownership and change history on every
channel.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models.file import Collection
from app.schemas.file import CollectionCreate, CollectionResponse, CollectionUpdate
from app.services.resources import rest
from app.services.resources.collections import CollectionApplier

router = APIRouter(prefix="/collections", tags=["collections"])

_applier = CollectionApplier()


@router.post("", response_model=CollectionResponse, status_code=status.HTTP_201_CREATED)
async def create_collection(
    request: Request,
    collection_data: CollectionCreate,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Create a new collection."""
    user_id, permissions = current_user_data

    # Check namespace-scoped permission to create collections
    permission = f"sinas.collections/{collection_data.namespace}/*.create:own"
    if not check_permission(permissions, permission):
        set_permission_used(request, permission, has_perm=False)
        raise HTTPException(
            status_code=403,
            detail=f"Not authorized to create collections in namespace '{collection_data.namespace}'"
        )
    set_permission_used(request, permission)

    ctx = rest.api_context(db, user_id)
    # A clash is a 400 "Collection 'ns/name' already exists", as before.
    result = await rest.write(
        _applier, ctx, rest.parse_spec(_applier, collection_data.model_dump()), must_create=True
    )
    await rest.commit(db, ctx)
    await db.refresh(result.obj)
    return CollectionResponse.model_validate(result.obj)


@router.get("", response_model=list[CollectionResponse])
async def list_collections(
    request: Request,
    namespace: str = None,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """List all collections accessible to the user."""
    user_id, permissions = current_user_data

    # Use mixin for permission-aware filtering. Workbenches (kind='workbench')
    # are chat-scoped working trees, never listed here.
    additional_filters = Collection.kind == "collection"
    if namespace:
        additional_filters = and_(additional_filters, Collection.namespace == namespace)

    collections = await Collection.list_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="read",
        additional_filters=additional_filters,
    )

    set_permission_used(request, "sinas.collections.read")

    return [CollectionResponse.model_validate(col) for col in collections]


@router.get("/{namespace}/{name}", response_model=CollectionResponse)
async def get_collection(
    namespace: str,
    name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Get a specific collection by namespace and name."""
    user_id, permissions = current_user_data

    # Use mixin for permission-aware get
    collection = await Collection.get_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="read",
        namespace=namespace,
        name=name,
    )

    if collection.kind != "collection":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found")

    set_permission_used(request, f"sinas.collections/{namespace}/{name}.read")

    return CollectionResponse.model_validate(collection)


@router.put("/{namespace}/{name}", response_model=CollectionResponse)
async def update_collection(
    namespace: str,
    name: str,
    collection_data: CollectionUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Update a collection's configuration."""
    user_id, permissions = current_user_data

    # Use mixin for permission-aware get
    collection = await Collection.get_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="update",
        namespace=namespace,
        name=name,
    )

    if collection.kind != "collection":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found")

    set_permission_used(request, f"sinas.collections/{namespace}/{name}.update")

    ctx = rest.api_context(db, user_id)
    collection = await rest.locked(_applier, ctx, collection)
    # As before: a field left out or sent as null stays as it is.
    patch = {
        field: value
        for field, value in collection_data.model_dump(exclude_unset=True).items()
        if value is not None
    }
    await rest.write(_applier, ctx, rest.patch_spec(_applier, collection, patch), existing=collection)
    await rest.commit(db, ctx)
    await db.refresh(collection)
    return CollectionResponse.model_validate(collection)


@router.delete("/{namespace}/{name}", status_code=204)
async def delete_collection(
    namespace: str,
    name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Delete a collection and all its files."""
    user_id, permissions = current_user_data

    # Use mixin for permission-aware get
    collection = await Collection.get_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="delete",
        namespace=namespace,
        name=name,
    )

    if collection.kind != "collection":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found")

    set_permission_used(request, f"sinas.collections/{namespace}/{name}.delete")

    ctx = rest.api_context(db, user_id)
    await _applier.delete(await rest.locked(_applier, ctx, collection), ctx)
    await rest.commit(db, ctx)
    return None
