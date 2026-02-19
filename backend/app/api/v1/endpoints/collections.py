"""Collection management endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models.file import Collection
from app.schemas.file import CollectionCreate, CollectionResponse, CollectionUpdate

router = APIRouter(prefix="/collections", tags=["collections"])


@router.post("", response_model=CollectionResponse)
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

    # Check if collection name already exists in this namespace
    result = await db.execute(
        select(Collection).where(
            and_(
                Collection.namespace == collection_data.namespace,
                Collection.name == collection_data.name
            )
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail=f"Collection '{collection_data.namespace}/{collection_data.name}' already exists",
        )

    # Create collection
    collection = Collection(
        user_id=user_id,
        namespace=collection_data.namespace,
        name=collection_data.name,
        metadata_schema=collection_data.metadata_schema,
        content_filter_function=collection_data.content_filter_function,
        post_upload_function=collection_data.post_upload_function,
        max_file_size_mb=collection_data.max_file_size_mb,
        max_total_size_gb=collection_data.max_total_size_gb,
        allow_shared_files=collection_data.allow_shared_files,
        allow_private_files=collection_data.allow_private_files,
    )

    db.add(collection)
    await db.commit()
    await db.refresh(collection)

    return CollectionResponse.model_validate(collection)


@router.get("", response_model=list[CollectionResponse])
async def list_collections(
    request: Request,
    namespace: str = None,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """List all collections accessible to the user."""
    user_id, permissions = current_user_data

    # Use mixin for permission-aware filtering
    additional_filters = None
    if namespace:
        additional_filters = Collection.namespace == namespace

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

    set_permission_used(request, f"sinas.collections/{namespace}/{name}.update")

    # Update fields
    if collection_data.metadata_schema is not None:
        collection.metadata_schema = collection_data.metadata_schema
    if collection_data.content_filter_function is not None:
        collection.content_filter_function = collection_data.content_filter_function
    if collection_data.post_upload_function is not None:
        collection.post_upload_function = collection_data.post_upload_function
    if collection_data.max_file_size_mb is not None:
        collection.max_file_size_mb = collection_data.max_file_size_mb
    if collection_data.max_total_size_gb is not None:
        collection.max_total_size_gb = collection_data.max_total_size_gb
    if collection_data.allow_shared_files is not None:
        collection.allow_shared_files = collection_data.allow_shared_files
    if collection_data.allow_private_files is not None:
        collection.allow_private_files = collection_data.allow_private_files

    await db.commit()
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

    set_permission_used(request, f"sinas.collections/{namespace}/{name}.delete")

    await db.delete(collection)
    await db.commit()

    return None
