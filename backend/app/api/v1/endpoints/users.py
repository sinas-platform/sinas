"""User management endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
import uuid

from app.core.database import get_db
from app.core.auth import get_current_user_with_permissions, set_permission_used, get_or_create_user
from app.core.permissions import check_permission
from app.models.user import User, Group, GroupMember
from app.schemas import UserResponse, UserWithGroupsResponse, UserUpdate
from app.schemas.auth import CreateUserRequest

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=List[UserResponse])
async def list_users(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """List users. Only admins can list all users."""
    user_id, permissions = current_user_data

    # Only admins can list users
    if not check_permission(permissions, "sinas.users.get:all"):
        set_permission_used(request, "sinas.users.get:all", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to list users")

    set_permission_used(request, "sinas.users.get:all")

    query = select(User).offset(skip).limit(limit)
    result = await db.execute(query)
    users = result.scalars().all()

    return users


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    request: Request,
    user_request: CreateUserRequest,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db)
):
    """
    Create a new user by email address.

    Only admins can create users. New users are assigned to the GuestUsers group by default.
    Requires permission: sinas.users.post:all
    """
    user_id, permissions = current_user_data

    # Check admin permission
    if not check_permission(permissions, "sinas.users.post:all"):
        set_permission_used(request, "sinas.users.post:all", has_perm=False)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to create users"
        )

    set_permission_used(request, "sinas.users.post:all")

    # Create user (assign to GuestUsers group, not Users group)
    user = await get_or_create_user(db, user_request.email, assign_to_users_group=False)

    # Check if already has groups
    memberships_result = await db.execute(
        select(GroupMember).where(GroupMember.user_id == user.id)
    )
    existing_memberships = memberships_result.scalars().all()

    # Only add to GuestUsers if no groups assigned yet
    if not existing_memberships:
        guest_group_result = await db.execute(
            select(Group).where(Group.name == "GuestUsers")
        )
        guest_group = guest_group_result.scalar_one_or_none()

        if guest_group:
            membership = GroupMember(
                group_id=guest_group.id,
                user_id=user.id,
                active=True
            )
            db.add(membership)
            await db.commit()
            await db.refresh(user)

    return UserResponse.model_validate(user)


@router.get("/{user_id}", response_model=UserWithGroupsResponse)
async def get_user(
    request: Request,
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Get a specific user with their groups."""
    current_user_id, permissions = current_user_data

    # Users can view their own profile, admins can view any user
    if str(user_id) == current_user_id:
        set_permission_used(request, "sinas.users.get:own")
    elif check_permission(permissions, "sinas.users.get:all"):
        set_permission_used(request, "sinas.users.get:all")
    else:
        set_permission_used(request, "sinas.users.get:all", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to view this user")

    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Get user's groups
    memberships_result = await db.execute(
        select(GroupMember).where(
            GroupMember.user_id == user_id,
            GroupMember.active == True
        )
    )
    memberships = memberships_result.scalars().all()

    # Get group names
    from app.models.user import Group
    group_names = []
    for membership in memberships:
        group_result = await db.execute(
            select(Group).where(Group.id == membership.group_id)
        )
        group = group_result.scalar_one_or_none()
        if group:
            group_names.append(group.name)

    return UserWithGroupsResponse(
        id=user.id,
        email=user.email,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        groups=group_names
    )


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    request: Request,
    user_id: uuid.UUID,
    user_data: UserUpdate,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Update a user. Only admins can update other users."""
    current_user_id, permissions = current_user_data

    # Users can update themselves, admins can update anyone
    if str(user_id) == current_user_id:
        set_permission_used(request, "sinas.users.put:own")
    elif check_permission(permissions, "sinas.users.put:all"):
        set_permission_used(request, "sinas.users.put:all")
    else:
        set_permission_used(request, "sinas.users.put:all", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to update this user")

    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Update fields (currently no updatable fields)
    # Future: Add updatable fields like display_name, etc.

    await db.commit()
    await db.refresh(user)

    return user


@router.delete("/{user_id}")
async def delete_user(
    request: Request,
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Delete a user. Only admins can delete users."""
    current_user_id, permissions = current_user_data

    if not check_permission(permissions, "sinas.users.delete:all"):
        set_permission_used(request, "sinas.users.delete:all", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to delete users")

    set_permission_used(request, "sinas.users.delete:all")

    # Prevent deleting yourself
    if str(user_id) == current_user_id:
        raise HTTPException(status_code=400, detail="Cannot delete your own user account")

    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    await db.delete(user)
    await db.commit()

    return {"message": f"User '{user.email}' deleted successfully"}
