"""Roles API endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from typing import List
import uuid

from app.core.database import get_db
from app.core.auth import get_current_user_with_permissions, require_permission, set_permission_used
from app.core.permissions import check_permission
from app.models.user import Role, UserRole, RolePermission, User
from app.schemas import (
    RoleCreate, RoleUpdate, RoleResponse,
    UserRoleResponse, UserRoleAdd,
    RolePermissionResponse, RolePermissionUpdate
)

router = APIRouter(prefix="/roles", tags=["roles"])


@router.post("", response_model=RoleResponse)
async def create_group(
    role_data: RoleCreate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(require_permission("sinas.roles.create:own"))
):
    """Create a new group. Requires admin permission."""

    # Check if group name already exists
    result = await db.execute(
        select(Role).where(Role.name == role_data.name)
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"Group '{role_data.name}' already exists")

    # Create group
    group = Role(
        name=role_data.name,
        description=role_data.description,
        email_domain=role_data.email_domain,
        external_role_id=role_data.external_role_id
    )

    db.add(group)
    await db.commit()
    await db.refresh(group)

    # Add creator as first member
    member = UserRole(
        role_id=group.id,
        user_id=uuid.UUID(user_id),
        role="admin",
        active=True,
        added_by=uuid.UUID(user_id)
    )
    db.add(member)
    await db.commit()

    return group


@router.get("", response_model=List[RoleResponse])
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
    if check_permission(permissions, "sinas.roles.read:all"):
        set_permission_used(request, "sinas.roles.read:all")
        query = select(Role)
    else:
        set_permission_used(request, "sinas.roles.read:own")
        # Regular users can only see groups they're a member of
        query = (
            select(Role)
            .join(UserRole, Role.id == UserRole.role_id)
            .where(
                and_(
                    UserRole.user_id == uuid.UUID(user_id),
                    UserRole.active == True
                )
            )
        )

    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    groups = result.scalars().all()

    return groups


@router.get("/{name}", response_model=RoleResponse)
async def get_group(
    request: Request,
    name: str,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Get a specific group."""
    user_id, permissions = current_user_data

    group = await Role.get_by_name(db, name)

    if not group:
        raise HTTPException(status_code=404, detail=f"Group '{name}' not found")

    # Check permissions
    if check_permission(permissions,"sinas.roles.read:all"):
        set_permission_used(request, "sinas.roles.read:all")
    else:
        # Check if user is a member
        membership_result = await db.execute(
            select(UserRole).where(
                and_(
                    UserRole.role_id == group.id,
                    UserRole.user_id == uuid.UUID(user_id),
                    UserRole.active == True
                )
            )
        )
        if not membership_result.scalar_one_or_none():
            set_permission_used(request, "sinas.roles.read:own", has_perm=False)
            raise HTTPException(status_code=403, detail="Not authorized to view this group")
        set_permission_used(request, "sinas.roles.read:own")

    return group


@router.patch("/{name}", response_model=RoleResponse)
async def update_group(
    request: Request,
    name: str,
    role_data: RoleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Update a group. Requires admin permission."""
    user_id, permissions = current_user_data

    group = await Role.get_by_name(db, name)

    if not group:
        raise HTTPException(status_code=404, detail=f"Group '{name}' not found")

    # Only admins can update groups
    if not check_permission(permissions,"sinas.roles.update:all"):
        set_permission_used(request, "sinas.roles.update:all", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to update groups")

    set_permission_used(request, "sinas.roles.update:all")

    # Update fields
    if role_data.name is not None:
        # Check if new name already exists
        result = await db.execute(
            select(Role).where(
                and_(
                    Role.name == role_data.name,
                    Role.id != group.id
                )
            )
        )
        if result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail=f"Group name '{role_data.name}' already exists")
        group.name = role_data.name

    if role_data.description is not None:
        group.description = role_data.description
    if role_data.email_domain is not None:
        group.email_domain = role_data.email_domain
    if role_data.external_role_id is not None:
        group.external_role_id = role_data.external_role_id

    await db.commit()
    await db.refresh(group)

    return group


@router.delete("/{name}")
async def delete_group(
    request: Request,
    name: str,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Delete a group. Only admins can delete groups."""
    user_id, permissions = current_user_data

    group = await Role.get_by_name(db, name)

    if not group:
        raise HTTPException(status_code=404, detail=f"Group '{name}' not found")

    # Only admins can delete groups
    if not check_permission(permissions,"sinas.roles.delete:all"):
        set_permission_used(request, "sinas.roles.delete:all", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to delete groups")

    set_permission_used(request, "sinas.roles.delete:all")

    await db.delete(group)
    await db.commit()

    return {"message": f"Group '{group.name}' deleted successfully"}


# Role Membership Management

@router.get("/{name}/members", response_model=List[UserRoleResponse])
async def list_group_members(
    request: Request,
    name: str,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """List members of a group."""
    user_id, permissions = current_user_data

    # Check if group exists
    group = await Role.get_by_name(db, name)
    if not group:
        raise HTTPException(status_code=404, detail=f"Group '{name}' not found")

    # Admins can see all, users can only see groups they're in
    if check_permission(permissions,"sinas.roles.read:all"):
        set_permission_used(request, "sinas.roles.read:all")
    else:
        membership_check = await db.execute(
            select(UserRole).where(
                and_(
                    UserRole.role_id == group.id,
                    UserRole.user_id == uuid.UUID(user_id),
                    UserRole.active == True
                )
            )
        )
        if not membership_check.scalar_one_or_none():
            set_permission_used(request, "sinas.roles.read:own", has_perm=False)
            raise HTTPException(status_code=403, detail="Not authorized to view this group")
        set_permission_used(request, "sinas.roles.read:own")

    # Join with User table to get email addresses
    result = await db.execute(
        select(UserRole, User.email).join(
            User, UserRole.user_id == User.id
        ).where(
            and_(
                UserRole.role_id == group.id,
                UserRole.active == True
            )
        )
    )
    rows = result.all()

    # Build response with user_email
    members_response = []
    for member, email in rows:
        members_response.append(UserRoleResponse(
            id=member.id,
            role_id=member.role_id,
            user_id=member.user_id,
            user_email=email,
            role=member.role,
            active=member.active,
            added_at=member.added_at
        ))

    return members_response


@router.post("/{name}/members", response_model=UserRoleResponse)
async def add_group_member(
    request: Request,
    name: str,
    member_data: UserRoleAdd,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Add a member to a group. Only admins can manage group members."""
    user_id, permissions = current_user_data

    if not check_permission(permissions,"sinas.roles.manage_members:all"):
        set_permission_used(request, "sinas.roles.manage_members:all", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to manage group members")

    set_permission_used(request, "sinas.roles.manage_members:all")

    # Check if group exists
    group = await Role.get_by_name(db, name)
    if not group:
        raise HTTPException(status_code=404, detail=f"Group '{name}' not found")

    # Check if user exists
    result = await db.execute(
        select(User).where(User.id == member_data.user_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if already a member
    result = await db.execute(
        select(UserRole).where(
            and_(
                UserRole.role_id == group.id,
                UserRole.user_id == member_data.user_id
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

        return UserRoleResponse(
            id=existing.id,
            role_id=existing.role_id,
            user_id=existing.user_id,
            user_email=user.email,
            role=existing.role,
            active=existing.active,
            added_at=existing.added_at
        )

    # Create new membership
    membership = UserRole(
        role_id=group.id,
        user_id=member_data.user_id,
        role=member_data.role,
        active=True,
        added_by=uuid.UUID(user_id)
    )

    db.add(membership)
    await db.commit()
    await db.refresh(membership)

    return UserRoleResponse(
        id=membership.id,
        role_id=membership.role_id,
        user_id=membership.user_id,
        user_email=user.email,
        role=membership.role,
        active=membership.active,
        added_at=membership.added_at
    )


@router.delete("/{name}/members/{user_id}")
async def remove_group_member(
    request: Request,
    name: str,
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Remove a member from a group. Only admins can manage group members."""
    current_user_id, permissions = current_user_data

    if not check_permission(permissions,"sinas.roles.manage_members:all"):
        set_permission_used(request, "sinas.roles.manage_members:all", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to manage group members")

    set_permission_used(request, "sinas.roles.manage_members:all")

    # Check if it's the Admins group
    group = await Role.get_by_name(db, name)
    if not group:
        raise HTTPException(status_code=404, detail=f"Group '{name}' not found")

    if group.name == "Admins":
        raise HTTPException(status_code=403, detail="Cannot remove members from Admins group")

    result = await db.execute(
        select(UserRole).where(
            and_(
                UserRole.role_id == group.id,
                UserRole.user_id == user_id,
                UserRole.active == True
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


# Role Permission Management

@router.get("/{name}/permissions", response_model=List[RolePermissionResponse])
async def list_group_permissions(
    request: Request,
    name: str,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """List permissions for a group. Only admins can view permissions."""
    user_id, permissions = current_user_data

    if not check_permission(permissions,"sinas.roles.manage_permissions:all"):
        set_permission_used(request, "sinas.roles.manage_permissions:all", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to view group permissions")

    set_permission_used(request, "sinas.roles.manage_permissions:all")

    # Check if group exists
    group = await Role.get_by_name(db, name)
    if not group:
        raise HTTPException(status_code=404, detail=f"Group '{name}' not found")

    result = await db.execute(
        select(RolePermission).where(RolePermission.role_id == group.id)
    )
    group_permissions = result.scalars().all()

    return group_permissions


@router.post("/{name}/permissions", response_model=RolePermissionResponse)
async def set_group_permission(
    request: Request,
    name: str,
    permission_data: RolePermissionUpdate,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Set a permission for a group. Only admins can manage permissions."""
    user_id, permissions = current_user_data

    if not check_permission(permissions,"sinas.roles.manage_permissions:all"):
        set_permission_used(request, "sinas.roles.manage_permissions:all", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to manage group permissions")

    set_permission_used(request, "sinas.roles.manage_permissions:all")

    # Check if group exists
    group = await Role.get_by_name(db, name)
    if not group:
        raise HTTPException(status_code=404, detail=f"Group '{name}' not found")

    # Prevent modifying Admins group permissions
    if group.name == "Admins":
        raise HTTPException(status_code=403, detail="Cannot modify Admins group permissions")

    # Check if permission already exists
    result = await db.execute(
        select(RolePermission).where(
            and_(
                RolePermission.role_id == group.id,
                RolePermission.permission_key == permission_data.permission_key
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
    new_permission = RolePermission(
        role_id=group.id,
        permission_key=permission_data.permission_key,
        permission_value=permission_data.permission_value
    )

    db.add(new_permission)
    await db.commit()
    await db.refresh(new_permission)

    return new_permission


@router.delete("/{name}/permissions/{permission_key}")
async def delete_group_permission(
    request: Request,
    name: str,
    permission_key: str,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Delete a permission from a group. Only admins can manage permissions."""
    user_id, permissions = current_user_data

    if not check_permission(permissions,"sinas.roles.manage_permissions:all"):
        set_permission_used(request, "sinas.roles.manage_permissions:all", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to manage group permissions")

    set_permission_used(request, "sinas.roles.manage_permissions:all")

    # Check if it's the Admins group
    group = await Role.get_by_name(db, name)
    if not group:
        raise HTTPException(status_code=404, detail=f"Group '{name}' not found")

    if group.name == "Admins":
        raise HTTPException(status_code=403, detail="Cannot modify Admins group permissions")

    result = await db.execute(
        select(RolePermission).where(
            and_(
                RolePermission.role_id == group.id,
                RolePermission.permission_key == permission_key
            )
        )
    )
    permission = result.scalar_one_or_none()

    if not permission:
        raise HTTPException(status_code=404, detail="Permission not found")

    await db.delete(permission)
    await db.commit()

    return {"message": "Permission deleted successfully"}
