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

from app.services.config_apply.normalizers import should_skip_existing

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


async def apply_pipelines(
    db: AsyncSession,
    pipelines: list,
    dry_run: bool,
    managed_by: str,
    config_name: str,
    owner_user_id: str,
    calculate_hash: Any,
    track_change: Any,
    errors: list[str],
    warnings: list[str],
) -> None:
    """Apply pipeline configurations.

    Shape validation runs here (steps, mapping expressions, perUser, asTool);
    cross-resource references (connectors/functions/agents/queries) are NOT
    checked — install order means they may not exist yet. Missing targets fail
    at run time with a clear error.
    """
    from app.models.pipeline import Pipeline
    from app.services.pipeline_validation import validate_pipeline_definition

    for pipe_config in pipelines:
        resource_name = f"{pipe_config.namespace}/{pipe_config.name}"
        try:
            output_mapping = pipe_config.output_mapping()
            validation_errors = validate_pipeline_definition(
                pipe_config.steps,
                per_user=pipe_config.perUser,
                as_tool=pipe_config.asTool,
                input_schema=pipe_config.inputSchema,
                description=pipe_config.description,
                tool_description=pipe_config.toolDescription,
                concurrency=pipe_config.concurrency,
                output_mapping=output_mapping,
            )
            if validation_errors:
                errors.append(
                    f"Invalid pipeline '{resource_name}': " + "; ".join(validation_errors)
                )
                continue

            stmt = select(Pipeline).where(
                Pipeline.namespace == pipe_config.namespace,
                Pipeline.name == pipe_config.name,
            )
            result = await db.execute(stmt)
            existing = result.scalar_one_or_none()

            config_hash = calculate_hash({
                "namespace": pipe_config.namespace,
                "name": pipe_config.name,
                "description": pipe_config.description,
                "input_schema": pipe_config.inputSchema or {},
                "steps": pipe_config.steps,
                "per_user": pipe_config.perUser,
                "as_tool": pipe_config.asTool,
                "tool_description": pipe_config.toolDescription,
                "sync_timeout_seconds": pipe_config.syncTimeoutSeconds,
                "concurrency": pipe_config.concurrency,
                "disable_after_failures": pipe_config.disableAfterFailures,
                "output_mapping": output_mapping,
                "is_active": pipe_config.isActive,
            })

            if existing:
                if should_skip_existing(existing, managed_by, config_name, config_hash, "pipelines", resource_name, track_change, warnings):
                    continue

                if not dry_run:
                    existing.description = pipe_config.description
                    existing.input_schema = pipe_config.inputSchema or {}
                    existing.steps = pipe_config.steps
                    existing.per_user = pipe_config.perUser
                    existing.as_tool = pipe_config.asTool
                    existing.tool_description = pipe_config.toolDescription
                    existing.sync_timeout_seconds = pipe_config.syncTimeoutSeconds
                    existing.concurrency = pipe_config.concurrency
                    existing.disable_after_failures = pipe_config.disableAfterFailures
                    existing.output_mapping = output_mapping
                    existing.is_active = pipe_config.isActive
                    if pipe_config.isActive:
                        # Reactivation clears the auto-disable state (cursor is kept).
                        existing.consecutive_failures = 0
                        existing.error_message = None
                    existing.managed_by = managed_by
                    existing.config_name = config_name
                    existing.config_checksum = config_hash

                track_change("update", "pipelines", resource_name)
            else:
                if not dry_run:
                    pipeline = Pipeline(
                        user_id=owner_user_id,
                        namespace=pipe_config.namespace,
                        name=pipe_config.name,
                        description=pipe_config.description,
                        input_schema=pipe_config.inputSchema or {},
                        steps=pipe_config.steps,
                        per_user=pipe_config.perUser,
                        as_tool=pipe_config.asTool,
                        tool_description=pipe_config.toolDescription,
                        sync_timeout_seconds=pipe_config.syncTimeoutSeconds,
                        concurrency=pipe_config.concurrency,
                        disable_after_failures=pipe_config.disableAfterFailures,
                        output_mapping=output_mapping,
                        is_active=pipe_config.isActive,
                        managed_by=managed_by,
                        config_name=config_name,
                        config_checksum=config_hash,
                    )
                    db.add(pipeline)

                track_change("create", "pipelines", resource_name)

        except Exception as e:
            errors.append(f"Failed to apply pipeline '{resource_name}': {e}")
            logger.exception(f"Error applying pipeline '{resource_name}'")
