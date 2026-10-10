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

from app.core.encryption import encryption_service
from app.models.dependency import Dependency
from app.models.function import Function, FunctionVersion
from app.models.secret import Secret

from app.services.config_apply.normalizers import should_skip_existing
from app.schemas.config import OwnershipSkip

logger = logging.getLogger(__name__)


async def apply_secrets(
    db: AsyncSession,
    secrets: list,
    dry_run: bool,
    managed_by: str,
    config_name: str,
    owner_user_id: str,
    calculate_hash: Any,
    track_change: Any,
    errors: list[str],
    warnings: list[str],
) -> None:
    """Apply secret configurations."""
    for secret_config in secrets:
        resource_name = secret_config.name
        try:
            # Scope to shared secrets. Config declares platform-level secrets,
            # while `private` rows are per-user overrides (see
            # connector_service._resolve_secret_value). Matching on name alone
            # could select — and then overwrite the value of — another user's
            # private secret. Shared names are globally unique (partial unique
            # index on name where visibility='shared'), so this stays a
            # single-row lookup.
            stmt = select(Secret).where(
                Secret.name == secret_config.name, Secret.visibility == "shared"
            )
            result = await db.execute(stmt)
            existing = result.scalar_one_or_none()

            # Hash only includes name (not value) so re-apply without value doesn't trigger update
            config_hash = calculate_hash(
                {
                    "name": secret_config.name,
                    "description": secret_config.description,
                }
            )

            if existing:
                if existing.managed_by and existing.managed_by != managed_by:
                    warnings.append(
                        OwnershipSkip(f"Secret '{resource_name}' exists but is managed by '{existing.managed_by}'. Skipping.")
                    )
                    track_change("unchanged", "secrets", resource_name)
                    continue

                # Always update value if provided, regardless of hash (secrets don't have is_active)
                needs_update = existing.config_checksum != config_hash or secret_config.value is not None

                if not needs_update:
                    track_change("unchanged", "secrets", resource_name)
                    continue

                if not dry_run:
                    if secret_config.value is not None:
                        existing.encrypted_value = encryption_service.encrypt(secret_config.value)
                    if secret_config.description is not None:
                        existing.description = secret_config.description
                    existing.managed_by = managed_by
                    existing.config_name = config_name
                    existing.config_checksum = config_hash

                track_change("update", "secrets", resource_name)
            else:
                if secret_config.value is None:
                    errors.append(
                        f"Secret '{resource_name}' does not exist and no value provided — cannot create."
                    )
                    continue

                if not dry_run:
                    secret = Secret(
                        user_id=owner_user_id,
                        name=secret_config.name,
                        # Explicit rather than relying on the model default:
                        # visibility decides who can read this, so it should be
                        # stated at the point of creation, not inherited.
                        visibility="shared",
                        encrypted_value=encryption_service.encrypt(secret_config.value),
                        description=secret_config.description,
                        managed_by=managed_by,
                        config_name=config_name,
                        config_checksum=config_hash,
                    )
                    db.add(secret)

                track_change("create", "secrets", resource_name)

        except Exception as e:
            errors.append(f"Failed to apply secret '{resource_name}': {e}")
            logger.exception(f"Error applying secret '{resource_name}'")


async def apply_functions(
    db: AsyncSession,
    functions: list,
    dry_run: bool,
    managed_by: str,
    config_name: str,
    owner_user_id: str,
    calculate_hash: Any,
    track_change: Any,
    errors: list[str],
    warnings: list[str],
    function_ids: dict[str, str],
) -> None:
    """Apply function configurations"""
    for func_config in functions:
        try:
            ns = getattr(func_config, "namespace", "default") or "default"
            stmt = select(Function).where(
                Function.namespace == ns, Function.name == func_config.name
            )
            result = await db.execute(stmt)
            existing = result.scalar_one_or_none()

            config_hash = calculate_hash(
                {
                    "namespace": ns,
                    "name": func_config.name,
                    "description": func_config.description,
                    "code": func_config.code,
                    "input_schema": func_config.inputSchema or {},
                    "output_schema": func_config.outputSchema or {},
                    "icon": func_config.icon,
                    "timeout": func_config.timeout,
                    "shared_pool": func_config.sharedPool,
                    "requires_approval": func_config.requiresApproval,
                }
            )

            if existing:
                if should_skip_existing(existing, managed_by, config_name, config_hash, "functions", f"{func_config.namespace}/{func_config.name}", track_change, warnings):
                    function_ids[func_config.name] = str(existing.id)
                    continue

                if not dry_run:
                    # Decide BEFORE overwriting: a version snapshot is only
                    # warranted when the executable surface (code/schemas)
                    # changes. Description/icon/timeout tweaks previously
                    # minted a new FunctionVersion on every apply — churn
                    # that made version history useless.
                    code_changed = (
                        existing.code != func_config.code
                        or (existing.input_schema or {}) != (func_config.inputSchema or {})
                        or (existing.output_schema or {}) != (func_config.outputSchema or {})
                    )

                    existing.description = func_config.description
                    existing.code = func_config.code
                    existing.input_schema = func_config.inputSchema or {}
                    existing.output_schema = func_config.outputSchema or {}
                    existing.icon = func_config.icon
                    existing.timeout = func_config.timeout
                    if func_config.sharedPool is not None:
                        existing.shared_pool = func_config.sharedPool
                    if func_config.requiresApproval is not None:
                        existing.requires_approval = func_config.requiresApproval
                    existing.is_active = True
                    existing.config_checksum = config_hash
                    existing.updated_at = datetime.utcnow()

                    if code_changed:
                        from sqlalchemy import func
                        max_ver_result = await db.execute(
                            select(func.coalesce(func.max(FunctionVersion.version), 0))
                            .where(FunctionVersion.function_id == existing.id)
                        )
                        max_ver = max_ver_result.scalar() or 0
                        version = FunctionVersion(
                            function_id=existing.id,
                            version=max_ver + 1,
                            code=func_config.code,
                            input_schema=func_config.inputSchema or {},
                            output_schema=func_config.outputSchema or {},
                            created_by=existing.user_id,
                        )
                        db.add(version)

                track_change("update", "functions", f"{func_config.namespace}/{func_config.name}")
                function_ids[func_config.name] = str(existing.id)

            else:
                if not dry_run:
                    new_function = Function(
                        namespace=ns,
                        name=func_config.name,
                        description=func_config.description,
                        code=func_config.code,
                        input_schema=func_config.inputSchema or {},
                        output_schema=func_config.outputSchema or {},
                        icon=func_config.icon,
                        timeout=func_config.timeout,
                        shared_pool=func_config.sharedPool or False,
                        requires_approval=func_config.requiresApproval or False,
                        user_id=owner_user_id,
                        is_active=True,
                        managed_by=managed_by,
                        config_name=config_name,
                        config_checksum=config_hash,
                    )
                    db.add(new_function)
                    await db.flush()

                    # Create initial version
                    version = FunctionVersion(
                        function_id=new_function.id,
                        version=1,
                        code=func_config.code,
                        input_schema=func_config.inputSchema or {},
                        output_schema=func_config.outputSchema or {},
                        created_by=owner_user_id,
                    )
                    db.add(version)
                    function_ids[func_config.name] = str(new_function.id)
                else:
                    function_ids[func_config.name] = "dry-run-id"

                track_change("create", "functions", f"{func_config.namespace}/{func_config.name}")

        except Exception as e:
            errors.append(f"Error applying function '{func_config.name}': {str(e)}")


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
