"""
Identity resource appliers: roles, role permissions, users, user roles
"""
import logging
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import Role, RolePermission, User, UserIdentity, UserRole
from app.schemas.config import ResourceChange

logger = logging.getLogger(__name__)


async def apply_roles(
    db: AsyncSession,
    roles: list,
    dry_run: bool,
    managed_by: str,
    config_name: str,
    calculate_hash: Any,
    track_change: Any,
    errors: list[str],
    warnings: list[str],
    role_ids: dict[str, str],
) -> None:
    """Apply role configurations"""
    for role_config in roles:
        try:
            # Check if exists
            stmt = select(Role).where(Role.name == role_config.name)
            result = await db.execute(stmt)
            existing = result.scalar_one_or_none()

            # Calculate hash. Permissions are part of it: without them, a
            # permission-only change hits the checksum-unchanged `continue`
            # below and is silently never applied (package upgrades are
            # mostly permission changes).
            config_hash = calculate_hash(
                {
                    "name": role_config.name,
                    "description": role_config.description,
                    "email_domain": role_config.emailDomain,
                    "permissions": sorted(
                        (p.key, p.value) for p in (role_config.permissions or [])
                    ),
                }
            )

            if existing:
                # Check if config-managed
                if existing.managed_by != managed_by:
                    warnings.append(
                        f"Role '{role_config.name}' exists but is not managed by '{managed_by}'. Skipping."
                    )
                    track_change("unchanged", "roles", role_config.name)
                    role_ids[role_config.name] = str(existing.id)
                    continue

                # Check if changed
                if existing.config_checksum == config_hash:
                    track_change("unchanged", "roles", role_config.name)
                    role_ids[role_config.name] = str(existing.id)
                    continue

                # Update
                if not dry_run:
                    existing.description = role_config.description
                    existing.email_domain = role_config.emailDomain
                    existing.config_checksum = config_hash
                    existing.updated_at = datetime.utcnow()

                track_change(
                    "update", "roles", role_config.name, details="Updated role configuration"
                )
                role_ids[role_config.name] = str(existing.id)

            else:
                # Create new
                if not dry_run:
                    new_role = Role(
                        name=role_config.name,
                        description=role_config.description,
                        email_domain=role_config.emailDomain,
                        managed_by=managed_by,
                        config_name=config_name,
                        config_checksum=config_hash,
                    )
                    db.add(new_role)
                    await db.flush()
                    role_ids[role_config.name] = str(new_role.id)
                else:
                    role_ids[role_config.name] = "dry-run-id"

                track_change(
                    "create", "roles", role_config.name, details="Created new role"
                )

            # Apply permissions
            if not dry_run and role_config.permissions:
                await apply_role_permissions(
                    db, role_ids[role_config.name], role_config.permissions
                )

        except Exception as e:
            errors.append(f"Error applying role '{role_config.name}': {str(e)}")


async def apply_role_permissions(
    db: AsyncSession, role_id: str, permissions: list
) -> None:
    """Apply permissions to a role"""
    # Delete existing permissions for this role
    stmt = delete(RolePermission).where(RolePermission.role_id == role_id)
    await db.execute(stmt)

    # Add new permissions
    for perm in permissions:
        perm_obj = RolePermission(
            role_id=role_id,
            permission_key=perm.key,
            permission_value=perm.value,
        )
        db.add(perm_obj)


async def apply_users(
    db: AsyncSession,
    users: list,
    dry_run: bool,
    managed_by: str,
    config_name: str,
    calculate_hash: Any,
    track_change: Any,
    errors: list[str],
    warnings: list[str],
    role_ids: dict[str, str],
    user_ids: dict[str, str],
) -> None:
    """Apply user configurations"""
    for user_config in users:
        try:
            stmt = select(User).where(User.email == user_config.email)
            result = await db.execute(stmt)
            existing = result.scalar_one_or_none()

            config_hash = calculate_hash(
                {
                    "email": user_config.email,
                    "roles": sorted(user_config.roles),
                    "customFields": user_config.customFields,
                    "identities": sorted(
                        ([i.provider, i.subject, i.metadata] for i in user_config.identities),
                        key=lambda x: (x[0], x[1]),
                    ),
                }
            )

            if existing:
                if existing.managed_by != managed_by:
                    warnings.append(
                        f"User '{user_config.email}' exists but is not managed by '{managed_by}'. Skipping."
                    )
                    track_change("unchanged", "users", user_config.email)
                    user_ids[user_config.email] = str(existing.id)
                    continue

                if existing.config_checksum == config_hash:
                    track_change("unchanged", "users", user_config.email)
                    user_ids[user_config.email] = str(existing.id)
                    continue

                if not dry_run:
                    existing.custom_fields = user_config.customFields
                    existing.config_checksum = config_hash
                    existing.updated_at = datetime.utcnow()

                track_change("update", "users", user_config.email)
                user_ids[user_config.email] = str(existing.id)

            else:
                if not dry_run:
                    new_user = User(
                        email=user_config.email,
                        custom_fields=user_config.customFields,
                        managed_by=managed_by,
                        config_name=config_name,
                        config_checksum=config_hash,
                    )
                    db.add(new_user)
                    await db.flush()
                    user_ids[user_config.email] = str(new_user.id)
                else:
                    user_ids[user_config.email] = "dry-run-id"

                track_change("create", "users", user_config.email)

            # Apply role memberships
            if not dry_run and user_config.roles:
                await apply_user_roles(
                    db, user_ids[user_config.email], user_config.roles,
                    role_ids, warnings,
                )

            # Apply external identities (declarative: config is the full set)
            if not dry_run:
                await apply_user_identities(
                    db, user_ids[user_config.email], user_config.identities, warnings
                )

        except Exception as e:
            errors.append(f"Error applying user '{user_config.email}': {str(e)}")


async def apply_user_identities(
    db: AsyncSession,
    user_id: str,
    identities: list,
    warnings: list[str],
) -> None:
    """
    Apply external identities to a config-managed user. The config is the
    full desired set: identities not in the config are removed, new ones are
    added, and metadata is refreshed on existing ones.
    """
    result = await db.execute(select(UserIdentity).where(UserIdentity.user_id == user_id))
    existing_by_key = {(i.provider, i.subject): i for i in result.scalars().all()}
    desired_keys = {(i.provider, i.subject) for i in identities}

    # Remove identities no longer in config
    for key, identity in existing_by_key.items():
        if key not in desired_keys:
            await db.delete(identity)

    for identity_config in identities:
        key = (identity_config.provider, identity_config.subject)
        existing = existing_by_key.get(key)

        if existing:
            existing.identity_metadata = identity_config.metadata
            existing.last_synced_at = datetime.utcnow()
            continue

        # Guard: identity may be linked to a different user
        stmt = select(UserIdentity).where(
            UserIdentity.provider == identity_config.provider,
            UserIdentity.subject == identity_config.subject,
        )
        conflict = (await db.execute(stmt)).scalar_one_or_none()
        if conflict:
            warnings.append(
                f"Identity '{identity_config.provider}:{identity_config.subject}' "
                f"is already linked to another user. Skipping."
            )
            continue

        db.add(
            UserIdentity(
                user_id=user_id,
                provider=identity_config.provider,
                subject=identity_config.subject,
                identity_metadata=identity_config.metadata,
                last_synced_at=datetime.utcnow(),
            )
        )


async def apply_user_roles(
    db: AsyncSession,
    user_id: str,
    role_names: list[str],
    role_ids: dict[str, str],
    warnings: list[str],
) -> None:
    """Apply role memberships to a user"""
    # Remove existing memberships for this user
    stmt = delete(UserRole).where(UserRole.user_id == user_id)
    await db.execute(stmt)

    # Add new memberships
    for role_name in role_names:
        if role_name not in role_ids:
            warnings.append(f"Role '{role_name}' not found for user membership")
            continue

        membership = UserRole(
            user_id=user_id,
            role_id=role_ids[role_name],
            active=True,
        )
        db.add(membership)
