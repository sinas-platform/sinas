"""API Key management endpoints."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import generate_api_key, get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission, validate_permission_subset
from app.models import APIKey, APIKeyRole, Role
from app.schemas.api_key import (
    APIKeyCreate,
    APIKeyCreated,
    APIKeyResponse,
    APIKeyRoleRef,
    APIKeyUpdate,
)

router = APIRouter()


async def _roles_by_key(db: AsyncSession, key_ids: list) -> dict[str, list[APIKeyRoleRef]]:
    """Role refs for a set of API keys, one query."""
    if not key_ids:
        return {}
    result = await db.execute(
        select(APIKeyRole.api_key_id, Role.id, Role.name)
        .join(Role, Role.id == APIKeyRole.role_id)
        .where(APIKeyRole.api_key_id.in_(key_ids))
    )
    by_key: dict[str, list[APIKeyRoleRef]] = {}
    for api_key_id, role_id, role_name in result.all():
        by_key.setdefault(str(api_key_id), []).append(APIKeyRoleRef(id=role_id, name=role_name))
    return by_key


def _key_response(key: APIKey, roles: list[APIKeyRoleRef]) -> APIKeyResponse:
    # Built explicitly (not from_attributes): the `roles` relationship is an
    # APIKeyRole list, not the {id, name} refs the schema carries, and lazy
    # loading it here would fail in async context anyway.
    return APIKeyResponse(
        id=key.id,
        user_id=key.user_id,
        name=key.name,
        key_prefix=key.key_prefix,
        permissions=key.permissions,
        roles=roles,
        is_active=key.is_active,
        last_used_at=key.last_used_at,
        expires_at=key.expires_at,
        created_at=key.created_at,
        revoked_at=key.revoked_at,
    )


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

    Scope the key with explicit `permissions` (validated as a subset of your
    own), with `role_ids` linking it to roles (the key then tracks the roles
    as they are edited), or both. Either way, effective permissions are capped
    by the owner's live permissions on every request.
    """
    user_id, permissions = current_user_data

    if not check_permission(permissions, "sinas.api_keys.create:own"):
        set_permission_used(http_request, "sinas.api_keys.create:own", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to create API keys")
    set_permission_used(http_request, "sinas.api_keys.create:own")

    # Explicit grants must be a subset of the creator's own permissions
    if request_data.permissions:
        is_valid, violations = validate_permission_subset(request_data.permissions, permissions)
        if not is_valid:
            raise HTTPException(
                status_code=400,
                detail=(
                    "API key permissions exceed your role permissions. "
                    f"Violations: {', '.join(violations)}"
                ),
            )

    # Resolve linked roles (existence only — effective permissions are capped
    # by the owner's live permissions at request time, so a role the owner
    # does not hold contributes nothing until they are assigned it)
    roles: list[Role] = []
    if request_data.role_ids:
        result = await db.execute(select(Role).where(Role.id.in_(request_data.role_ids)))
        roles = list(result.scalars().all())
        missing = {str(r) for r in request_data.role_ids} - {str(r.id) for r in roles}
        if missing:
            raise HTTPException(
                status_code=400, detail=f"Unknown role ids: {', '.join(sorted(missing))}"
            )

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
    await db.flush()

    for role in roles:
        db.add(APIKeyRole(api_key_id=api_key.id, role_id=role.id))
    await db.flush()
    await db.refresh(api_key)

    # Return response with plain key (only time it's shown)
    return APIKeyCreated(
        id=api_key.id,
        name=api_key.name,
        key=plain_key,  # Plain key - only shown once!
        key_prefix=api_key.key_prefix,
        permissions=api_key.permissions,
        roles=[APIKeyRoleRef(id=r.id, name=r.name) for r in roles],
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

    roles_by_key = await _roles_by_key(db, key_ids)

    # Build response with user email for admins
    responses = []
    for key in api_keys_sorted:
        response = _key_response(key, roles_by_key.get(str(key.id), []))
        # Add user email if viewing with :all scope (admin)
        if key.user:
            response.user_email = key.user.email
        responses.append(response)

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

    roles_by_key = await _roles_by_key(db, [api_key.id])
    return _key_response(api_key, roles_by_key.get(str(api_key.id), []))


@router.patch("/api-keys/{key_id}", response_model=APIKeyResponse)
async def update_api_key(
    key_id: str,
    request_data: APIKeyUpdate,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """
    Update an API key's name, explicit permissions, or linked roles in place —
    the key value never changes, so no rotation. Omitted fields are left
    unchanged; provided permission/role sets REPLACE the previous ones.
    """
    user_id, permissions = current_user_data

    api_key = await APIKey.get_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="update",
        resource_id=key_id,
    )
    set_permission_used(http_request, "sinas.api_keys.update")

    if request_data.permissions is not None and request_data.permissions:
        # Same mint-time guard as create: the updater cannot grant beyond
        # their own permissions (the live cap bounds requests regardless)
        is_valid, violations = validate_permission_subset(request_data.permissions, permissions)
        if not is_valid:
            raise HTTPException(
                status_code=400,
                detail=(
                    "API key permissions exceed your role permissions. "
                    f"Violations: {', '.join(violations)}"
                ),
            )

    roles: list[Role] = []
    if request_data.role_ids is not None:
        if request_data.role_ids:
            result = await db.execute(select(Role).where(Role.id.in_(request_data.role_ids)))
            roles = list(result.scalars().all())
            missing = {str(r) for r in request_data.role_ids} - {str(r.id) for r in roles}
            if missing:
                raise HTTPException(
                    status_code=400, detail=f"Unknown role ids: {', '.join(sorted(missing))}"
                )
        await db.execute(delete(APIKeyRole).where(APIKeyRole.api_key_id == api_key.id))
        for role in roles:
            db.add(APIKeyRole(api_key_id=api_key.id, role_id=role.id))

    if request_data.name is not None:
        api_key.name = request_data.name
    if request_data.permissions is not None:
        api_key.permissions = request_data.permissions

    await db.flush()

    roles_by_key = await _roles_by_key(db, [api_key.id])
    return _key_response(api_key, roles_by_key.get(str(api_key.id), []))


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

    await db.flush()

    return None
