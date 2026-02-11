"""API Key management endpoints."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import generate_api_key, get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.models import APIKey
from app.schemas.api_key import APIKeyCreate, APIKeyCreated, APIKeyResponse

router = APIRouter()


@router.post("/api-keys", response_model=APIKeyCreated, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    request_data: APIKeyCreate,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """
    Create a new API key for the current user.

    The plain API key is returned only once - store it securely!
    """
    user_id, permissions = current_user_data
    set_permission_used(http_request, "sinas.api_keys.create:own")

    # Generate API key
    plain_key, key_hash, key_prefix = generate_api_key()

    # Create API key record
    api_key = APIKey(
        user_id=user_id,
        name=request_data.name,
        key_hash=key_hash,
        key_prefix=key_prefix,
        permissions=request_data.permissions or {},
        expires_at=request_data.expires_at,
        is_active=True,
        created_by=user_id,
    )

    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)

    # Return response with plain key (only time it's shown)
    return APIKeyCreated(
        id=api_key.id,
        name=api_key.name,
        key=plain_key,  # Plain key - only shown once!
        key_prefix=api_key.key_prefix,
        permissions=api_key.permissions,
        expires_at=api_key.expires_at,
        created_at=api_key.created_at,
    )


@router.get("/api-keys", response_model=list[APIKeyResponse])
async def list_api_keys(
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """
    List all API keys accessible to the user.

    - Users with :own scope see their own keys
    - Users with :all scope see all keys
    """
    user_id, permissions = current_user_data

    # Use PermissionMixin for permission-aware filtering
    from sqlalchemy.orm import selectinload

    api_keys = await APIKey.list_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="read",
        additional_filters=None,
        skip=0,
        limit=1000,
    )

    # Eagerly load user relationship for all keys
    key_ids = [key.id for key in api_keys]
    if key_ids:
        result = await db.execute(
            select(APIKey).where(APIKey.id.in_(key_ids)).options(selectinload(APIKey.user))
        )
        loaded_keys = {str(k.id): k for k in result.scalars().all()}
        # Replace keys with loaded versions that have user relationship
        api_keys = [loaded_keys[str(k.id)] for k in api_keys if str(k.id) in loaded_keys]

    set_permission_used(http_request, "sinas.api_keys.read")

    # Sort by created_at desc
    api_keys_sorted = sorted(api_keys, key=lambda k: k.created_at, reverse=True)

    # Build response with user email for admins
    responses = []
    for key in api_keys_sorted:
        response_data = APIKeyResponse.model_validate(key).model_dump()
        # Add user email if viewing with :all scope (admin)
        if key.user:
            response_data["user_email"] = key.user.email
        responses.append(APIKeyResponse(**response_data))

    return responses


@router.get("/api-keys/{key_id}", response_model=APIKeyResponse)
async def get_api_key(
    key_id: str,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """
    Get details of a specific API key (own or all if admin).
    """
    user_id, permissions = current_user_data

    # Use PermissionMixin for permission-aware get
    api_key = await APIKey.get_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="read",
        resource_id=key_id,
    )

    if not api_key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")

    set_permission_used(http_request, "sinas.api_keys.read")

    return APIKeyResponse.model_validate(api_key)


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    key_id: str,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """
    Revoke (soft delete) an API key (own or all if admin).
    """
    user_id, permissions = current_user_data

    # Use PermissionMixin for permission-aware get
    api_key = await APIKey.get_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="delete",
        resource_id=key_id,
    )

    if not api_key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")

    set_permission_used(http_request, "sinas.api_keys.delete")

    # Soft delete: mark as revoked
    api_key.is_active = False
    api_key.revoked_at = datetime.utcnow()
    api_key.revoked_by = user_id

    await db.commit()

    return None
