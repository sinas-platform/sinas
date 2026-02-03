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
    List all API keys for the current user.
    """
    user_id, permissions = current_user_data
    set_permission_used(http_request, "sinas.api_keys.read:own")

    result = await db.execute(
        select(APIKey).where(APIKey.user_id == user_id).order_by(APIKey.created_at.desc())
    )
    api_keys = result.scalars().all()

    return [APIKeyResponse.model_validate(key) for key in api_keys]


@router.get("/api-keys/{key_id}", response_model=APIKeyResponse)
async def get_api_key(
    key_id: str,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """
    Get details of a specific API key.
    """
    user_id, permissions = current_user_data
    set_permission_used(http_request, "sinas.api_keys.read:own")

    api_key = await db.get(APIKey, key_id)

    if not api_key or str(api_key.user_id) != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")

    return APIKeyResponse.model_validate(api_key)


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    key_id: str,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """
    Revoke (soft delete) an API key.
    """
    user_id, permissions = current_user_data
    set_permission_used(http_request, "sinas.api_keys.delete:own")

    api_key = await db.get(APIKey, key_id)

    if not api_key or str(api_key.user_id) != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")

    # Soft delete: mark as revoked
    api_key.is_active = False
    api_key.revoked_at = datetime.utcnow()
    api_key.revoked_by = user_id

    await db.commit()

    return None
