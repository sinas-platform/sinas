"""Secrets API endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import and_, or_, select
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
    """Create or update a secret (upsert by name+visibility). Value is encrypted before storage."""
    user_id, permissions = current_user_data

    permission = "sinas.secrets.create:own"
    if not check_permission(permissions, permission):
        set_permission_used(request, permission, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to manage secrets")
    set_permission_used(request, permission)

    # Find existing secret to upsert
    if secret_data.visibility == "private":
        result = await db.execute(
            select(Secret).where(
                and_(Secret.name == secret_data.name, Secret.user_id == user_id, Secret.visibility == "private")
            )
        )
    else:
        result = await db.execute(
            select(Secret).where(
                and_(Secret.name == secret_data.name, Secret.visibility == "shared")
            )
        )
    existing = result.scalar_one_or_none()

    if existing:
        existing.encrypted_value = encryption_service.encrypt(secret_data.value)
        if secret_data.description is not None:
            existing.description = secret_data.description
        await db.flush()
        await db.refresh(existing)
        return SecretResponse.model_validate(existing)

    secret = Secret(
        user_id=user_id,
        name=secret_data.name,
        encrypted_value=encryption_service.encrypt(secret_data.value),
        description=secret_data.description,
        visibility=secret_data.visibility,
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
    """List secrets: all shared + user's own private."""
    user_id, permissions = current_user_data

    permission = "sinas.secrets.read:own"
    if not check_permission(permissions, permission):
        set_permission_used(request, permission, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to list secrets")
    set_permission_used(request, permission)

    has_all = check_permission(permissions, "sinas.secrets.read:all")

    if has_all:
        # Admin: see everything
        result = await db.execute(select(Secret))
    else:
        # User: shared secrets + own private secrets
        result = await db.execute(
            select(Secret).where(
                or_(Secret.visibility == "shared", Secret.user_id == user_id)
            )
        )

    return [SecretResponse.model_validate(s) for s in result.scalars().all()]


@router.get("/{name}", response_model=SecretResponse)
async def get_secret(
    request: Request,
    name: str,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Get secret metadata (no value returned). Returns private if exists, else shared."""
    user_id, permissions = current_user_data

    permission = "sinas.secrets.read:own"
    if not check_permission(permissions, permission):
        set_permission_used(request, permission, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to read secrets")
    set_permission_used(request, permission)

    # Try private first, then shared
    result = await db.execute(
        select(Secret).where(
            and_(Secret.name == name, Secret.user_id == user_id, Secret.visibility == "private")
        )
    )
    secret = result.scalar_one_or_none()

    if not secret:
        result = await db.execute(
            select(Secret).where(and_(Secret.name == name, Secret.visibility == "shared"))
        )
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
    """Update a secret's value or description. Updates private if exists, else shared."""
    user_id, permissions = current_user_data

    permission = "sinas.secrets.update:own"
    if not check_permission(permissions, permission):
        set_permission_used(request, permission, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to update secrets")
    set_permission_used(request, permission)

    # Try private first, then shared
    result = await db.execute(
        select(Secret).where(
            and_(Secret.name == name, Secret.user_id == user_id, Secret.visibility == "private")
        )
    )
    secret = result.scalar_one_or_none()

    if not secret:
        result = await db.execute(
            select(Secret).where(and_(Secret.name == name, Secret.visibility == "shared"))
        )
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
    visibility: str = "shared",
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Delete a secret."""
    user_id, permissions = current_user_data

    permission = "sinas.secrets.delete:own"
    if not check_permission(permissions, permission):
        set_permission_used(request, permission, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to delete secrets")
    set_permission_used(request, permission)

    if visibility == "private":
        result = await db.execute(
            select(Secret).where(
                and_(Secret.name == name, Secret.user_id == user_id, Secret.visibility == "private")
            )
        )
    else:
        result = await db.execute(
            select(Secret).where(and_(Secret.name == name, Secret.visibility == "shared"))
        )
    secret = result.scalar_one_or_none()

    if not secret:
        raise HTTPException(status_code=404, detail=f"Secret '{name}' not found")

    await db.delete(secret)
    await db.flush()
    return None
