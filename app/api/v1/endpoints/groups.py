"""Groups API endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from typing import List
import uuid

from app.core.database import get_db
from app.core.auth import get_current_user_with_permissions, require_permission, set_permission_used
from app.models.user import Group, GroupMember, GroupPermission, User
from app.schemas import (
    GroupCreate, GroupUpdate, GroupResponse,
    GroupMemberResponse, GroupMemberAdd,
    GroupPermissionResponse, GroupPermissionUpdate
)

router = APIRouter(prefix="/groups", tags=["groups"])


@router.post("", response_model=GroupResponse)
async def create_group(
    group_data: GroupCreate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(require_permission("sinas.groups.create:own"))
):
    """Create a new group. Requires admin permission."""

    # Check if group name already exists
    result = await db.execute(
        select(Group).where(Group.name == group_data.name)
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"Group '{group_data.name}' already exists")

    # Create group
    group = Group(
        name=group_data.name,
        description=group_data.description,
        email_domain=group_data.email_domain
    )

    db.add(group)
    await db.commit()
    await db.refresh(group)

    # Add creator as first member
    member = GroupMember(
        group_id=group.id,
        user_id=uuid.UUID(user_id),
        role="admin",
        active=True,
        added_by=uuid.UUID(user_id)
    )
    db.add(member)
    await db.commit()

    return group


@router.get("", response_model=List[GroupResponse])
async def list_groups(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """List groups accessible to the current user."""
    user_id, permissions = current_user_data

    # Admins can see all groups
    if permissions.get("sinas.groups.read:all"):
        set_permission_used(request, "sinas.groups.read:all")
        query = select(Group)
    else:
        set_permission_used(request, "sinas.groups.read:own")
        # Regular users can only see groups they're a member of
        query = (
            select(Group)
            .join(GroupMember, Group.id == GroupMember.group_id)
            .where(
                and_(
                    GroupMember.user_id == uuid.UUID(user_id),
                    GroupMember.active == True
                )
            )
        )

    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    groups = result.scalars().all()

    return groups


@router.get("/{group_id}", response_model=GroupResponse)
async def get_group(
    request: Request,
    group_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Get a specific group."""
    user_id, permissions = current_user_data

    result = await db.execute(
        select(Group).where(Group.id == group_id)
    )
    group = result.scalar_one_or_none()

    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    # Check permissions
    if permissions.get("sinas.groups.read:all"):
        set_permission_used(request, "sinas.groups.read:all")
    else:
        # Check if user is a member
        membership_result = await db.execute(
            select(GroupMember).where(
                and_(
                    GroupMember.group_id == group_id,
                    GroupMember.user_id == uuid.UUID(user_id),
                    GroupMember.active == True
                )
            )
        )
        if not membership_result.scalar_one_or_none():
            set_permission_used(request, "sinas.groups.read:own", has_perm=False)
            raise HTTPException(status_code=403, detail="Not authorized to view this group")
        set_permission_used(request, "sinas.groups.read:own")

    return group


@router.patch("/{group_id}", response_model=GroupResponse)
async def update_group(
    request: Request,
    group_id: uuid.UUID,
    group_data: GroupUpdate,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Update a group. Requires admin permission."""
    user_id, permissions = current_user_data

    result = await db.execute(
        select(Group).where(Group.id == group_id)
    )
    group = result.scalar_one_or_none()

    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    # Only admins can update groups
    if not permissions.get("sinas.groups.update:all"):
        set_permission_used(request, "sinas.groups.update:all", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to update groups")

    set_permission_used(request, "sinas.groups.update:all")

    # Update fields
    if group_data.name is not None:
        # Check if new name already exists
        result = await db.execute(
            select(Group).where(
                and_(
                    Group.name == group_data.name,
                    Group.id != group_id
                )
            )
        )
        if result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail=f"Group name '{group_data.name}' already exists")
        group.name = group_data.name

    if group_data.description is not None:
        group.description = group_data.description
    if group_data.email_domain is not None:
        group.email_domain = group_data.email_domain

    await db.commit()
    await db.refresh(group)

    return group


@router.delete("/{group_id}")
async def delete_group(
    request: Request,
    group_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Delete a group. Only admins can delete groups."""
    user_id, permissions = current_user_data

    result = await db.execute(
        select(Group).where(Group.id == group_id)
    )
    group = result.scalar_one_or_none()

    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    # Only admins can delete groups
    if not permissions.get("sinas.groups.delete:all"):
        set_permission_used(request, "sinas.groups.delete:all", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to delete groups")

    set_permission_used(request, "sinas.groups.delete:all")

    await db.delete(group)
    await db.commit()

    return {"message": f"Group '{group.name}' deleted successfully"}


# Group Membership Management

@router.get("/{group_id}/members", response_model=List[GroupMemberResponse])
async def list_group_members(
    request: Request,
    group_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """List members of a group."""
    user_id, permissions = current_user_data

    # Check if group exists
    result = await db.execute(
        select(Group).where(Group.id == group_id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Group not found")

    # Admins can see all, users can only see groups they're in
    if permissions.get("sinas.groups.read:all"):
        set_permission_used(request, "sinas.groups.read:all")
    else:
        membership_check = await db.execute(
            select(GroupMember).where(
                and_(
                    GroupMember.group_id == group_id,
                    GroupMember.user_id == uuid.UUID(user_id),
                    GroupMember.active == True
                )
            )
        )
        if not membership_check.scalar_one_or_none():
            set_permission_used(request, "sinas.groups.read:own", has_perm=False)
            raise HTTPException(status_code=403, detail="Not authorized to view this group")
        set_permission_used(request, "sinas.groups.read:own")

    result = await db.execute(
        select(GroupMember).where(
            and_(
                GroupMember.group_id == group_id,
                GroupMember.active == True
            )
        )
    )
    members = result.scalars().all()

    return members


@router.post("/{group_id}/members", response_model=GroupMemberResponse)
async def add_group_member(
    request: Request,
    group_id: uuid.UUID,
    member_data: GroupMemberAdd,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Add a member to a group. Only admins can manage group members."""
    user_id, permissions = current_user_data

    if not permissions.get("sinas.groups.manage_members:all"):
        set_permission_used(request, "sinas.groups.manage_members:all", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to manage group members")

    set_permission_used(request, "sinas.groups.manage_members:all")

    # Check if group exists
    result = await db.execute(
        select(Group).where(Group.id == group_id)
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    # Check if user exists
    result = await db.execute(
        select(User).where(User.id == member_data.user_id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="User not found")

    # Check if already a member
    result = await db.execute(
        select(GroupMember).where(
            and_(
                GroupMember.group_id == group_id,
                GroupMember.user_id == member_data.user_id
            )
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        if existing.active:
            raise HTTPException(status_code=400, detail="User is already a member of this group")
        # Reactivate membership
        existing.active = True
        existing.role = member_data.role
        existing.added_by = uuid.UUID(user_id)
        await db.commit()
        await db.refresh(existing)
        return existing

    # Create new membership
    membership = GroupMember(
        group_id=group_id,
        user_id=member_data.user_id,
        role=member_data.role,
        active=True,
        added_by=uuid.UUID(user_id)
    )

    db.add(membership)
    await db.commit()
    await db.refresh(membership)

    return membership


@router.delete("/{group_id}/members/{user_id}")
async def remove_group_member(
    request: Request,
    group_id: uuid.UUID,
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Remove a member from a group. Only admins can manage group members."""
    current_user_id, permissions = current_user_data

    if not permissions.get("sinas.groups.manage_members:all"):
        set_permission_used(request, "sinas.groups.manage_members:all", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to manage group members")

    set_permission_used(request, "sinas.groups.manage_members:all")

    # Check if it's the Admins group
    result = await db.execute(
        select(Group).where(Group.id == group_id)
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    if group.name == "Admins":
        raise HTTPException(status_code=403, detail="Cannot remove members from Admins group")

    result = await db.execute(
        select(GroupMember).where(
            and_(
                GroupMember.group_id == group_id,
                GroupMember.user_id == user_id,
                GroupMember.active == True
            )
        )
    )
    membership = result.scalar_one_or_none()

    if not membership:
        raise HTTPException(status_code=404, detail="Membership not found")

    # Soft delete by marking inactive
    from datetime import datetime as dt
    membership.active = False
    membership.removed_at = dt.utcnow()
    membership.removed_by = uuid.UUID(current_user_id)

    await db.commit()

    return {"message": "Member removed from group successfully"}


# Group Permission Management

@router.get("/{group_id}/permissions", response_model=List[GroupPermissionResponse])
async def list_group_permissions(
    request: Request,
    group_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """List permissions for a group. Only admins can view permissions."""
    user_id, permissions = current_user_data

    if not permissions.get("sinas.groups.manage_permissions:all"):
        set_permission_used(request, "sinas.groups.manage_permissions:all", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to view group permissions")

    set_permission_used(request, "sinas.groups.manage_permissions:all")

    # Check if group exists
    result = await db.execute(
        select(Group).where(Group.id == group_id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Group not found")

    result = await db.execute(
        select(GroupPermission).where(GroupPermission.group_id == group_id)
    )
    group_permissions = result.scalars().all()

    return group_permissions


@router.post("/{group_id}/permissions", response_model=GroupPermissionResponse)
async def set_group_permission(
    request: Request,
    group_id: uuid.UUID,
    permission_data: GroupPermissionUpdate,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Set a permission for a group. Only admins can manage permissions."""
    user_id, permissions = current_user_data

    if not permissions.get("sinas.groups.manage_permissions:all"):
        set_permission_used(request, "sinas.groups.manage_permissions:all", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to manage group permissions")

    set_permission_used(request, "sinas.groups.manage_permissions:all")

    # Check if group exists
    result = await db.execute(
        select(Group).where(Group.id == group_id)
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    # Prevent modifying Admins group permissions
    if group.name == "Admins":
        raise HTTPException(status_code=403, detail="Cannot modify Admins group permissions")

    # Check if permission already exists
    result = await db.execute(
        select(GroupPermission).where(
            and_(
                GroupPermission.group_id == group_id,
                GroupPermission.permission_key == permission_data.permission_key
            )
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        existing.permission_value = permission_data.permission_value
        await db.commit()
        await db.refresh(existing)
        return existing

    # Create new permission
    new_permission = GroupPermission(
        group_id=group_id,
        permission_key=permission_data.permission_key,
        permission_value=permission_data.permission_value
    )

    db.add(new_permission)
    await db.commit()
    await db.refresh(new_permission)

    return new_permission


@router.delete("/{group_id}/permissions/{permission_key}")
async def delete_group_permission(
    request: Request,
    group_id: uuid.UUID,
    permission_key: str,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Delete a permission from a group. Only admins can manage permissions."""
    user_id, permissions = current_user_data

    if not permissions.get("sinas.groups.manage_permissions:all"):
        set_permission_used(request, "sinas.groups.manage_permissions:all", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to manage group permissions")

    set_permission_used(request, "sinas.groups.manage_permissions:all")

    # Check if it's the Admins group
    result = await db.execute(
        select(Group).where(Group.id == group_id)
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    if group.name == "Admins":
        raise HTTPException(status_code=403, detail="Cannot modify Admins group permissions")

    result = await db.execute(
        select(GroupPermission).where(
            and_(
                GroupPermission.group_id == group_id,
                GroupPermission.permission_key == permission_key
            )
        )
    )
    permission = result.scalar_one_or_none()

    if not permission:
        raise HTTPException(status_code=404, detail="Permission not found")

    await db.delete(permission)
    await db.commit()

    return {"message": "Permission deleted successfully"}
