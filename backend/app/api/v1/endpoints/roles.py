"""Roles API endpoints."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, require_permission, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models.user import Role, RolePermission, User, UserRole
from app.schemas import (
    RoleCreate,
    RolePermissionResponse,
    RolePermissionUpdate,
    RoleResponse,
    RoleUpdate,
    UserRoleAdd,
    UserRoleResponse,
)

router = APIRouter(prefix="/roles", tags=["roles"])


@router.post("", response_model=RoleResponse)
async def create_role(
    role_data: RoleCreate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(require_permission("sinas.roles.create:own")),
):
    """Create a new role. Requires admin permission."""

    # Check if role name already exists
    result = await db.execute(select(Role).where(Role.name == role_data.name))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"Role '{role_data.name}' already exists")

    # Create role
    role = Role(
        name=role_data.name,
        description=role_data.description,
        email_domain=role_data.email_domain,
        external_role_id=role_data.external_role_id,
    )

    db.add(role)
    await db.commit()
    await db.refresh(role)

    # Add creator as first member
    member = UserRole(
        role_id=role.id,
        user_id=uuid.UUID(user_id),
        active=True,
        added_by=uuid.UUID(user_id),
    )
    db.add(member)
    await db.commit()

    return role


@router.get("", response_model=list[RoleResponse])
async def list_roles(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """List roles accessible to the current user."""
    user_id, permissions = current_user_data

    # Admins can see all roles
    if check_permission(permissions, "sinas.roles.read:all"):
        set_permission_used(request, "sinas.roles.read:all")
        query = select(Role)
    else:
        set_permission_used(request, "sinas.roles.read:own")
        # Regular users can only see roles they're a member of
        query = (
            select(Role)
            .join(UserRole, Role.id == UserRole.role_id)
            .where(and_(UserRole.user_id == uuid.UUID(user_id), UserRole.active == True))
        )

    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    roles = result.scalars().all()

    return roles


@router.get("/{name}", response_model=RoleResponse)
async def get_role(
    request: Request,
    name: str,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Get a specific role."""
    user_id, permissions = current_user_data

    role = await Role.get_by_name(db, name)

    if not role:
        raise HTTPException(status_code=404, detail=f"Role '{name}' not found")

    # Check permissions
    if check_permission(permissions, "sinas.roles.read:all"):
        set_permission_used(request, "sinas.roles.read:all")
    else:
        # Check if user is a member
        membership_result = await db.execute(
            select(UserRole).where(
                and_(
                    UserRole.role_id == role.id,
                    UserRole.user_id == uuid.UUID(user_id),
                    UserRole.active == True,
                )
            )
        )
        if not membership_result.scalar_one_or_none():
            set_permission_used(request, "sinas.roles.read:own", has_perm=False)
            raise HTTPException(status_code=403, detail="Not authorized to view this role")
        set_permission_used(request, "sinas.roles.read:own")

    return role


@router.patch("/{name}", response_model=RoleResponse)
async def update_role(
    request: Request,
    name: str,
    role_data: RoleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Update a role. Requires admin permission."""
    user_id, permissions = current_user_data

    role = await Role.get_by_name(db, name)

    if not role:
        raise HTTPException(status_code=404, detail=f"Role '{name}' not found")

    # Only admins can update roles
    if not check_permission(permissions, "sinas.roles.update:all"):
        set_permission_used(request, "sinas.roles.update:all", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to update roles")

    set_permission_used(request, "sinas.roles.update:all")

    # Update fields
    if role_data.name is not None:
        # Check if new name already exists
        result = await db.execute(
            select(Role).where(and_(Role.name == role_data.name, Role.id != role.id))
        )
        if result.scalar_one_or_none():
            raise HTTPException(
                status_code=400, detail=f"Role name '{role_data.name}' already exists"
            )
        role.name = role_data.name

    if role_data.description is not None:
        role.description = role_data.description
    if role_data.email_domain is not None:
        role.email_domain = role_data.email_domain
    if role_data.external_role_id is not None:
        role.external_role_id = role_data.external_role_id

    await db.commit()
    await db.refresh(role)

    return role


@router.delete("/{name}")
async def delete_role(
    request: Request,
    name: str,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Delete a role. Only admins can delete roles."""
    user_id, permissions = current_user_data

    role = await Role.get_by_name(db, name)

    if not role:
        raise HTTPException(status_code=404, detail=f"Role '{name}' not found")

    # Only admins can delete roles
    if not check_permission(permissions, "sinas.roles.delete:all"):
        set_permission_used(request, "sinas.roles.delete:all", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to delete roles")

    set_permission_used(request, "sinas.roles.delete:all")

    await db.delete(role)
    await db.commit()

    return {"message": f"Role '{role.name}' deleted successfully"}


# Role Membership Management


@router.get("/{name}/members", response_model=list[UserRoleResponse])
async def list_role_members(
    request: Request,
    name: str,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """List members of a role."""
    user_id, permissions = current_user_data

    # Check if role exists
    role = await Role.get_by_name(db, name)
    if not role:
        raise HTTPException(status_code=404, detail=f"Role '{name}' not found")

    # Only admins can view role members
    if not check_permission(permissions, "sinas.roles.read:all"):
        set_permission_used(request, "sinas.roles.read:all", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to view role members")

    set_permission_used(request, "sinas.roles.read:all")

    # Join with User table to get email addresses
    result = await db.execute(
        select(UserRole, User.email)
        .join(User, UserRole.user_id == User.id)
        .where(and_(UserRole.role_id == role.id, UserRole.active == True))
    )
    rows = result.all()

    # Build response with user_email
    members_response = []
    for member, email in rows:
        members_response.append(
            UserRoleResponse(
                id=member.id,
                role_id=member.role_id,
                user_id=member.user_id,
                user_email=email,
                active=member.active,
                added_at=member.added_at,
            )
        )

    return members_response


@router.post("/{name}/members", response_model=UserRoleResponse)
async def add_role_member(
    request: Request,
    name: str,
    member_data: UserRoleAdd,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Add a member to a role. Only admins can manage role members."""
    user_id, permissions = current_user_data

    if not check_permission(permissions, "sinas.roles.manage_members:all"):
        set_permission_used(request, "sinas.roles.manage_members:all", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to manage role members")

    set_permission_used(request, "sinas.roles.manage_members:all")

    # Check if role exists
    role = await Role.get_by_name(db, name)
    if not role:
        raise HTTPException(status_code=404, detail=f"Role '{name}' not found")

    # Check if user exists
    result = await db.execute(select(User).where(User.id == member_data.user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if already a member
    result = await db.execute(
        select(UserRole).where(
            and_(UserRole.role_id == role.id, UserRole.user_id == member_data.user_id)
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        if existing.active:
            raise HTTPException(status_code=400, detail="User is already a member of this role")
        # Reactivate membership
        existing.active = True
        existing.added_by = uuid.UUID(user_id)
        await db.commit()
        await db.refresh(existing)

        return UserRoleResponse(
            id=existing.id,
            role_id=existing.role_id,
            user_id=existing.user_id,
            user_email=user.email,
            active=existing.active,
            added_at=existing.added_at,
        )

    # Create new membership
    membership = UserRole(
        role_id=role.id,
        user_id=member_data.user_id,
        active=True,
        added_by=uuid.UUID(user_id),
    )

    db.add(membership)
    await db.commit()
    await db.refresh(membership)

    return UserRoleResponse(
        id=membership.id,
        role_id=membership.role_id,
        user_id=membership.user_id,
        user_email=user.email,
        active=membership.active,
        added_at=membership.added_at,
    )


@router.delete("/{name}/members/{user_id}")
async def remove_role_member(
    request: Request,
    name: str,
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Remove a member from a role. Only admins can manage role members."""
    current_user_id, permissions = current_user_data

    if not check_permission(permissions, "sinas.roles.manage_members:all"):
        set_permission_used(request, "sinas.roles.manage_members:all", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to manage role members")

    set_permission_used(request, "sinas.roles.manage_members:all")

    # Check if it's the Admins role
    role = await Role.get_by_name(db, name)
    if not role:
        raise HTTPException(status_code=404, detail=f"Role '{name}' not found")

    if role.name == "Admins":
        raise HTTPException(status_code=403, detail="Cannot remove members from Admins role")

    result = await db.execute(
        select(UserRole).where(
            and_(UserRole.role_id == role.id, UserRole.user_id == user_id, UserRole.active == True)
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

    return {"message": "Member removed from role successfully"}


# Role Permission Management


@router.get("/{name}/permissions", response_model=list[RolePermissionResponse])
async def list_role_permissions(
    request: Request,
    name: str,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """List permissions for a role. Only admins can view permissions."""
    user_id, permissions = current_user_data

    if not check_permission(permissions, "sinas.roles.manage_permissions:all"):
        set_permission_used(request, "sinas.roles.manage_permissions:all", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to view role permissions")

    set_permission_used(request, "sinas.roles.manage_permissions:all")

    # Check if role exists
    role = await Role.get_by_name(db, name)
    if not role:
        raise HTTPException(status_code=404, detail=f"Role '{name}' not found")

    result = await db.execute(select(RolePermission).where(RolePermission.role_id == role.id))
    role_permissions = result.scalars().all()

    return role_permissions


@router.post("/{name}/permissions", response_model=RolePermissionResponse)
async def set_role_permission(
    request: Request,
    name: str,
    permission_data: RolePermissionUpdate,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Set a permission for a role. Only admins can manage permissions."""
    user_id, permissions = current_user_data

    if not check_permission(permissions, "sinas.roles.manage_permissions:all"):
        set_permission_used(request, "sinas.roles.manage_permissions:all", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to manage role permissions")

    set_permission_used(request, "sinas.roles.manage_permissions:all")

    # Check if role exists
    role = await Role.get_by_name(db, name)
    if not role:
        raise HTTPException(status_code=404, detail=f"Role '{name}' not found")

    # Prevent modifying Admins role permissions
    if role.name == "Admins":
        raise HTTPException(status_code=403, detail="Cannot modify Admins role permissions")

    # Check if permission already exists
    result = await db.execute(
        select(RolePermission).where(
            and_(
                RolePermission.role_id == role.id,
                RolePermission.permission_key == permission_data.permission_key,
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
        role_id=role.id,
        permission_key=permission_data.permission_key,
        permission_value=permission_data.permission_value,
    )

    db.add(new_permission)
    await db.commit()
    await db.refresh(new_permission)

    return new_permission


@router.delete("/{name}/permissions")
async def delete_role_permission(
    request: Request,
    name: str,
    permission_key: str = Query(..., description="Permission key to delete"),
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Delete a permission from a role. Only admins can manage permissions."""
    user_id, permissions = current_user_data

    if not check_permission(permissions, "sinas.roles.manage_permissions:all"):
        set_permission_used(request, "sinas.roles.manage_permissions:all", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to manage role permissions")

    set_permission_used(request, "sinas.roles.manage_permissions:all")

    # Check if it's the Admins role
    role = await Role.get_by_name(db, name)
    if not role:
        raise HTTPException(status_code=404, detail=f"Role '{name}' not found")

    if role.name == "Admins":
        raise HTTPException(status_code=403, detail="Cannot modify Admins role permissions")

    result = await db.execute(
        select(RolePermission).where(
            and_(
                RolePermission.role_id == role.id, RolePermission.permission_key == permission_key
            )
        )
    )
    permission = result.scalar_one_or_none()

    if not permission:
        raise HTTPException(status_code=404, detail="Permission not found")

    await db.delete(permission)
    await db.commit()

    return {"message": "Permission deleted successfully"}
