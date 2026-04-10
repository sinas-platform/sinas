"""User management endpoints."""
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.auth import get_current_user_with_permissions, normalize_email, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models.user import Role, User, UserRole
from app.schemas import UserResponse, UserUpdate
from app.schemas.auth import CreateUserRequest
from app.schemas.user import UserWithRolesResponse

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserWithRolesResponse])
async def list_users(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    search: Optional[str] = Query(None, description="Search by email"),
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """List users with their roles. Only admins can list all users."""
    user_id, permissions = current_user_data

    # Only admins can list users
    if not check_permission(permissions, "sinas.users.read:all"):
        set_permission_used(request, "sinas.users.read:all", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to list users")

    set_permission_used(request, "sinas.users.read:all")

    query = select(User)
    if search:
        query = query.where(User.email.ilike(f"%{search}%"))
    query = query.order_by(User.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(query)
    users = result.scalars().all()

    # Batch-load role names for all users
    user_ids = [u.id for u in users]
    if user_ids:
        memberships_result = await db.execute(
            select(UserRole.user_id, Role.name)
            .join(Role, Role.id == UserRole.role_id)
            .where(UserRole.user_id.in_(user_ids), UserRole.active == True)
        )
        roles_by_user: dict[uuid.UUID, list[str]] = {}
        for uid, role_name in memberships_result.all():
            roles_by_user.setdefault(uid, []).append(role_name)
    else:
        roles_by_user = {}

    return [
        UserWithRolesResponse(
            id=u.id,
            email=u.email,
            last_login_at=u.last_login_at,
            created_at=u.created_at,
            roles=roles_by_user.get(u.id, []),
        )
        for u in users
    ]


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    request: Request,
    user_request: CreateUserRequest,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a new user by email address.

    Only admins can create users. New users are assigned to the GuestUsers role by default.
    Requires permission: sinas.users.post:all
    """
    user_id, permissions = current_user_data

    # Check admin permission
    if not check_permission(permissions, "sinas.users.create:all"):
        set_permission_used(request, "sinas.users.create:all", has_perm=False)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to create users"
        )

    set_permission_used(request, "sinas.users.create:all")

    # Check if user already exists
    normalized_email = normalize_email(user_request.email)

    result = await db.execute(select(User).where(User.email == normalized_email))
    user = result.scalar_one_or_none()

    if user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"User with email '{user_request.email}' already exists",
        )

    # Create new user
    user = User(email=normalized_email)
    db.add(user)
    await db.flush()

    # Check if already has roles
    memberships_result = await db.execute(select(UserRole).where(UserRole.user_id == user.id))
    existing_memberships = memberships_result.scalars().all()

    # Only add to GuestUsers if no roles assigned yet
    if not existing_memberships:
        guest_role_result = await db.execute(select(Role).where(Role.name == "GuestUsers"))
        guest_role = guest_role_result.scalar_one_or_none()

        if guest_role:
            membership = UserRole(role_id=guest_role.id, user_id=user.id, active=True)
            db.add(membership)
            await db.flush()
            await db.refresh(user)

    return UserResponse.model_validate(user)


@router.get("/{user_id}", response_model=UserWithRolesResponse)
async def get_user(
    request: Request,
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Get a specific user with their roles."""
    current_user_id, permissions = current_user_data

    # Use mixin for permission-aware get
    user = await User.get_with_permissions(
        db=db,
        user_id=current_user_id,
        permissions=permissions,
        action="read",
        resource_id=user_id,
    )

    set_permission_used(request, "sinas.users.read")

    # Get user's roles
    memberships_result = await db.execute(
        select(UserRole).where(UserRole.user_id == user_id, UserRole.active == True)
    )
    memberships = memberships_result.scalars().all()

    role_names = []
    for membership in memberships:
        role_result = await db.execute(select(Role).where(Role.id == membership.role_id))
        role = role_result.scalar_one_or_none()
        if role:
            role_names.append(role.name)

    return UserWithRolesResponse(
        id=user.id,
        email=user.email,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        roles=role_names,
    )


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    request: Request,
    user_id: uuid.UUID,
    user_data: UserUpdate,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Update a user. Only admins can update other users."""
    current_user_id, permissions = current_user_data

    # Use mixin for permission-aware get
    user = await User.get_with_permissions(
        db=db,
        user_id=current_user_id,
        permissions=permissions,
        action="update",
        resource_id=user_id,
    )

    set_permission_used(request, "sinas.users.update")

    # Update fields (currently no updatable fields)
    # Future: Add updatable fields like display_name, etc.

    await db.flush()
    await db.refresh(user)

    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    request: Request,
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Delete a user. Only admins can delete users."""
    current_user_id, permissions = current_user_data

    # Prevent deleting yourself
    if str(user_id) == current_user_id:
        raise HTTPException(status_code=400, detail="Cannot delete your own user account")

    # Use mixin for permission-aware get
    user = await User.get_with_permissions(
        db=db,
        user_id=current_user_id,
        permissions=permissions,
        action="delete",
        resource_id=user_id,
    )

    # Prevent deleting the superadmin
    import os
    superadmin_email = os.environ.get("SUPERADMIN_EMAIL", "")
    if user.email and user.email.lower() == superadmin_email.lower():
        raise HTTPException(status_code=403, detail="Cannot delete the superadmin account")

    set_permission_used(request, "sinas.users.delete")

    # Soft delete: deactivate user and remove all role memberships
    user.is_active = False
    from app.models.user import UserRole
    from datetime import datetime as dt
    result = await db.execute(
        select(UserRole).where(UserRole.user_id == user_id, UserRole.active == True)
    )
    for membership in result.scalars().all():
        membership.active = False
        membership.removed_at = dt.utcnow()
        membership.removed_by = uuid.UUID(current_user_id)

    await db.flush()

    return None
