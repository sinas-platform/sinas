"""Secrets API endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.encryption import encryption_service
from app.core.permissions import check_permission
from app.models.secret import Secret
from app.schemas.secret import SecretCreate, SecretResponse, SecretUpdate

router = APIRouter(prefix="/secrets", tags=["secrets"])


@router.post("", response_model=SecretResponse, status_code=status.HTTP_201_CREATED)
async def create_or_update_secret(
    request: Request,
    secret_data: SecretCreate,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Create or update a secret (upsert by name). Value is encrypted before storage."""
    user_id, permissions = current_user_data

    # Check for existing secret
    result = await db.execute(select(Secret).where(Secret.name == secret_data.name))
    existing = result.scalar_one_or_none()

    if existing:
        permission = "sinas.secrets.update:own"
        if not check_permission(permissions, permission):
            set_permission_used(request, permission, has_perm=False)
            raise HTTPException(status_code=403, detail="Not authorized to update secrets")
        set_permission_used(request, permission)

        existing.encrypted_value = encryption_service.encrypt(secret_data.value)
        if secret_data.description is not None:
            existing.description = secret_data.description
        await db.flush()
        await db.refresh(existing)
        return SecretResponse.model_validate(existing)
    else:
        permission = "sinas.secrets.create:own"
        if not check_permission(permissions, permission):
            set_permission_used(request, permission, has_perm=False)
            raise HTTPException(status_code=403, detail="Not authorized to create secrets")
        set_permission_used(request, permission)

        secret = Secret(
            user_id=user_id,
            name=secret_data.name,
            encrypted_value=encryption_service.encrypt(secret_data.value),
            description=secret_data.description,
        )
        db.add(secret)
        await db.flush()
        await db.refresh(secret)
        return SecretResponse.model_validate(secret)


@router.get("", response_model=list[SecretResponse])
async def list_secrets(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """List all secrets (names and descriptions only, no values). Global resource."""
    _user_id, permissions = current_user_data

    permission = "sinas.secrets.read:own"
    if not check_permission(permissions, permission):
        set_permission_used(request, permission, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to list secrets")
    set_permission_used(request, permission)

    result = await db.execute(select(Secret))
    return [SecretResponse.model_validate(s) for s in result.scalars().all()]


@router.get("/{name}", response_model=SecretResponse)
async def get_secret(
    request: Request,
    name: str,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Get secret metadata (no value returned)."""
    _user_id, permissions = current_user_data

    permission = "sinas.secrets.read:own"
    if not check_permission(permissions, permission):
        set_permission_used(request, permission, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to read secrets")
    set_permission_used(request, permission)

    result = await db.execute(select(Secret).where(Secret.name == name))
    secret = result.scalar_one_or_none()
    if not secret:
        raise HTTPException(status_code=404, detail=f"Secret '{name}' not found")

    return SecretResponse.model_validate(secret)


@router.put("/{name}", response_model=SecretResponse)
async def update_secret(
    request: Request,
    name: str,
    secret_data: SecretUpdate,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Update a secret's value or description."""
    _user_id, permissions = current_user_data

    permission = "sinas.secrets.update:own"
    if not check_permission(permissions, permission):
        set_permission_used(request, permission, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to update secrets")
    set_permission_used(request, permission)

    result = await db.execute(select(Secret).where(Secret.name == name))
    secret = result.scalar_one_or_none()
    if not secret:
        raise HTTPException(status_code=404, detail=f"Secret '{name}' not found")

    if secret_data.value is not None:
        secret.encrypted_value = encryption_service.encrypt(secret_data.value)
    if secret_data.description is not None:
        secret.description = secret_data.description

    await db.flush()
    await db.refresh(secret)
    return SecretResponse.model_validate(secret)


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_secret(
    request: Request,
    name: str,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Delete a secret."""
    _user_id, permissions = current_user_data

    permission = "sinas.secrets.delete:own"
    if not check_permission(permissions, permission):
        set_permission_used(request, permission, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to delete secrets")
    set_permission_used(request, permission)

    result = await db.execute(select(Secret).where(Secret.name == name))
    secret = result.scalar_one_or_none()
    if not secret:
        raise HTTPException(status_code=404, detail=f"Secret '{name}' not found")

    await db.delete(secret)
    await db.flush()
    return None
