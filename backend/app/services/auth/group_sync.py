"""Group synchronization for external authentication."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)


async def sync_user_groups(
    db: AsyncSession,
    user: "User",
    external_group_ids: List[str]
):
    """
    Sync external groups to SINAS group memberships.
    Uses 1:1 mapping via Group.external_group_id field.

    Args:
        db: Database session
        user: User to sync groups for
        external_group_ids: List of external group IDs from IdP
    """
    from app.core.config import settings
    from app.models import Group, GroupMember

    logger.info(f"Syncing {len(external_group_ids)} external groups for user {user.email}")

    # Find SINAS groups by external_group_id (1:1 mapping)
    result = await db.execute(
        select(Group).where(Group.external_group_id.in_(external_group_ids))
    )
    matched_groups = {g.external_group_id: g for g in result.scalars().all()}
    target_groups = set(matched_groups.values())

    logger.info(f"Found {len(matched_groups)} mapped groups")

    # Auto-provision unmapped groups if enabled
    if settings.auto_provision_groups:
        unmapped_ids = set(external_group_ids) - set(matched_groups.keys())
        for ext_id in unmapped_ids:
            logger.info(f"Auto-provisioning group for external ID: {ext_id}")
            group = Group(
                name=ext_id,  # Use external ID as name
                description=f"Auto-provisioned from external IdP",
                external_group_id=ext_id
            )
            db.add(group)
            await db.flush()
            target_groups.add(group)

    # Fallback to default group if no matches
    if not target_groups:
        logger.info(f"No groups matched, assigning to default group: {settings.default_group_name}")
        result = await db.execute(
            select(Group).where(Group.name == settings.default_group_name)
        )
        default_group = result.scalar_one_or_none()
        if default_group:
            target_groups.add(default_group)
        else:
            logger.warning(f"Default group '{settings.default_group_name}' not found")

    # Get user's current active memberships
    result = await db.execute(
        select(GroupMember).where(
            GroupMember.user_id == user.id,
            GroupMember.active == True
        )
    )
    current_memberships = {m.group_id: m for m in result.scalars().all()}

    # Add missing memberships
    target_group_ids = {g.id for g in target_groups}
    for group_id in target_group_ids:
        if group_id not in current_memberships:
            logger.info(f"Adding user to group {group_id}")
            membership = GroupMember(
                user_id=user.id,
                group_id=group_id,
                active=True
            )
            db.add(membership)

    # Remove memberships for external-managed groups user no longer has
    # Only deactivate groups that have external_group_id set (external-managed)
    result = await db.execute(
        select(Group.id).where(Group.external_group_id.isnot(None))
    )
    external_managed_ids = {row[0] for row in result.all()}

    for group_id, membership in current_memberships.items():
        if group_id in external_managed_ids and group_id not in target_group_ids:
            logger.info(f"Removing user from external group {group_id}")
            membership.active = False
            membership.removed_at = datetime.now(timezone.utc)

    # Update last sync timestamp
    user.last_external_sync = datetime.now(timezone.utc)

    await db.commit()
    logger.info(f"Group sync completed for user {user.email}")
