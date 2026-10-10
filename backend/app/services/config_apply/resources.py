"""
Resource appliers: queries, functions, skills, components, collections, stores, manifests, dependencies
"""
import logging
import uuid as uuid_lib
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from datetime import timezone as tz

from app.models.dependency import Dependency


logger = logging.getLogger(__name__)


async def apply_dependencies(
    db: AsyncSession,
    dependencies: list,
    dry_run: bool,
    owner_user_id: str,
    track_change: Any,
    errors: list[str],
    warnings: list[str],
    **_kwargs,
):
    """Apply dependency (Python package) configurations.

    Upserts into the dependencies table. Actual installation in containers
    happens on worker restart/rebuild — this just records the approval.
    """
    for dep_config in dependencies:
        package_name = dep_config.packageName
        version = dep_config.version

        # Guard: split "package==1.2.3" into name + version if baked together
        if "==" in package_name:
            parts = package_name.split("==", 1)
            package_name = parts[0]
            if not version:
                version = parts[1]

        try:
            existing = await db.execute(
                select(Dependency).where(Dependency.package_name == package_name)
            )
            existing_dep = existing.scalar_one_or_none()

            if existing_dep:
                # Update version if changed
                if version and existing_dep.version != version:
                    if not dry_run:
                        existing_dep.version = version
                        existing_dep.installed_at = datetime.now(tz.utc)
                    track_change("update", "dependencies", package_name)
                else:
                    track_change("unchanged", "dependencies", package_name)
            else:
                if not dry_run:
                    dep = Dependency(
                        package_name=package_name,
                        version=version,
                        installed_at=datetime.now(tz.utc),
                        installed_by=uuid_lib.UUID(owner_user_id) if owner_user_id else None,
                    )
                    db.add(dep)
                track_change("create", "dependencies", package_name)

        except Exception as e:
            errors.append(f"Error applying dependency '{package_name}': {str(e)}")
