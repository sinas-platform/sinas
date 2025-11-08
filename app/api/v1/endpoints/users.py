"""User management endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
import uuid

from app.core.database import get_db
from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.models.user import User, GroupMember
from app.schemas import UserResponse, UserWithGroupsResponse, UserUpdate

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
    if not permissions.get("sinas.users.read:all"):
        set_permission_used(request, "sinas.users.read:all", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to list users")

    set_permission_used(request, "sinas.users.read:all")

    query = select(User).offset(skip).limit(limit)
    result = await db.execute(query)
    users = result.scalars().all()

    return users


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
        set_permission_used(request, "sinas.users.read:own")
    elif permissions.get("sinas.users.read:all"):
        set_permission_used(request, "sinas.users.read:all")
    else:
        set_permission_used(request, "sinas.users.read:all", has_perm=False)
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
        is_active=user.is_active,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
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
        set_permission_used(request, "sinas.users.update:own")
    elif permissions.get("sinas.users.update:all"):
        set_permission_used(request, "sinas.users.update:all")
    else:
        set_permission_used(request, "sinas.users.update:all", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to update this user")

    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Update fields
    if user_data.is_active is not None:
        # Only admins can change active status
        if not permissions.get("sinas.users.update:all"):
            raise HTTPException(status_code=403, detail="Not authorized to change user status")
        user.is_active = user_data.is_active

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

    if not permissions.get("sinas.users.delete:all"):
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
