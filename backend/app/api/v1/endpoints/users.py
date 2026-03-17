"""User management endpoints."""
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.auth import get_current_user_with_permissions, normalize_email, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models.user import Role, User, UserRole
from app.schemas import UserResponse, UserUpdate, UserWithGroupsResponse
from app.schemas.auth import CreateUserRequest

router = APIRouter(prefix="/users", tags=["users"])


class UserWithRolesResponse(BaseModel):
    id: uuid.UUID
    email: str
    last_login_at: Optional[datetime] = None
    created_at: datetime
    roles: list[str]

    class Config:
        from_attributes = True


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

    Only admins can create users. New users are assigned to the GuestUsers group by default.
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
    await db.flush()  # Get user ID before adding to group

    # Check if already has groups
    memberships_result = await db.execute(select(UserRole).where(UserRole.user_id == user.id))
    existing_memberships = memberships_result.scalars().all()

    # Only add to GuestUsers if no groups assigned yet
    if not existing_memberships:
        guest_group_result = await db.execute(select(Role).where(Role.name == "GuestUsers"))
        guest_group = guest_group_result.scalar_one_or_none()

        if guest_group:
            membership = UserRole(role_id=guest_group.id, user_id=user.id, active=True)
            db.add(membership)
            await db.flush()
            await db.refresh(user)

    return UserResponse.model_validate(user)


@router.get("/{user_id}", response_model=UserWithGroupsResponse)
async def get_user(
    request: Request,
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Get a specific user with their groups."""
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

    # Get user's groups
    memberships_result = await db.execute(
        select(UserRole).where(UserRole.user_id == user_id, UserRole.active == True)
    )
    memberships = memberships_result.scalars().all()

    # Get group names
    group_names = []
    for membership in memberships:
        group_result = await db.execute(select(Role).where(Role.id == membership.role_id))
        group = group_result.scalar_one_or_none()
        if group:
            group_names.append(group.name)

    return UserWithGroupsResponse(
        id=user.id,
        email=user.email,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        groups=group_names,
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


@router.delete("/{user_id}")
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

    set_permission_used(request, "sinas.users.delete")

    await db.delete(user)
    await db.flush()

    return {"message": f"User '{user.email}' deleted successfully"}
