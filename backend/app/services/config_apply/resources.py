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
from app.models.component import Component
from app.models.database_connection import DatabaseConnection
from app.models.dependency import Dependency
from app.models.file import Collection
from app.models.function import Function, FunctionVersion
from app.models.manifest import Manifest
from app.models.query import Query
from app.models.connector import Connector
from app.models.secret import Secret
from app.models.skill import Skill
from app.models.store import Store

from app.services.config_apply.normalizers import normalize_store_references, should_skip_existing

logger = logging.getLogger(__name__)


async def apply_connectors(
    db: AsyncSession,
    connectors: list,
    dry_run: bool,
    managed_by: str,
    config_name: str,
    owner_user_id: str,
    calculate_hash: Any,
    track_change: Any,
    errors: list[str],
    warnings: list[str],
) -> None:
    """Apply connector configurations."""
    for conn_config in connectors:
        resource_name = f"{conn_config.namespace}/{conn_config.name}"
        try:
            stmt = select(Connector).where(
                Connector.namespace == conn_config.namespace,
                Connector.name == conn_config.name,
            )
            result = await db.execute(stmt)
            existing = result.scalar_one_or_none()

            # Convert operations to dicts
            operations = []
            for op in conn_config.operations:
                operations.append({
                    "name": op.name,
                    "method": op.method,
                    "path": op.path,
                    "description": op.description,
                    "parameters": op.parameters,
                    "request_body_mapping": op.requestBodyMapping,
                    "response_mapping": op.responseMapping,
                })

            auth = {
                "type": conn_config.auth.type,
                "secret": conn_config.auth.secret,
                "header": conn_config.auth.header,
                "position": conn_config.auth.position,
                "param_name": conn_config.auth.paramName,
            }
            # Remove None values from auth
            auth = {k: v for k, v in auth.items() if v is not None}

            retry = {
                "max_attempts": conn_config.retry.maxAttempts,
                "backoff": conn_config.retry.backoff,
            }

            config_hash = calculate_hash({
                "namespace": conn_config.namespace,
                "name": conn_config.name,
                "base_url": conn_config.baseUrl,
                "auth": auth,
                "headers": conn_config.headers,
                "retry": retry,
                "timeout_seconds": conn_config.timeoutSeconds,
                "operations": operations,
            })

            if existing:
                if should_skip_existing(existing, managed_by, config_name, config_hash, "connectors", resource_name, track_change, warnings):
                    continue

                if not dry_run:
                    existing.base_url = conn_config.baseUrl
                    existing.description = conn_config.description
                    existing.auth = auth
                    existing.headers = conn_config.headers
                    existing.retry = retry
                    existing.timeout_seconds = conn_config.timeoutSeconds
                    existing.operations = operations
                    existing.is_active = True
                    existing.managed_by = managed_by
                    existing.config_name = config_name
                    existing.config_checksum = config_hash

                track_change("update", "connectors", resource_name)
            else:
                if not dry_run:
                    connector = Connector(
                        user_id=owner_user_id,
                        namespace=conn_config.namespace,
                        name=conn_config.name,
                        description=conn_config.description,
                        base_url=conn_config.baseUrl,
                        auth=auth,
                        headers=conn_config.headers,
                        retry=retry,
                        timeout_seconds=conn_config.timeoutSeconds,
                        operations=operations,
                        managed_by=managed_by,
                        config_name=config_name,
                        config_checksum=config_hash,
                    )
                    db.add(connector)

                track_change("create", "connectors", resource_name)

        except Exception as e:
            errors.append(f"Failed to apply connector '{resource_name}': {e}")
            logger.exception(f"Error applying connector '{resource_name}'")


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
            stmt = select(Secret).where(Secret.name == secret_config.name)
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
                        f"Secret '{resource_name}' exists but is managed by '{existing.managed_by}'. Skipping."
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


async def apply_queries(
    db: AsyncSession,
    queries: list,
    dry_run: bool,
    managed_by: str,
    config_name: str,
    owner_user_id: str,
    calculate_hash: Any,
    track_change: Any,
    errors: list[str],
    warnings: list[str],
    database_connection_ids: dict[str, str],
) -> None:
    """Apply query configurations"""
    for query_config in queries:
        resource_name = f"{query_config.namespace}/{query_config.name}"
        try:
            stmt = select(Query).where(
                Query.namespace == query_config.namespace,
                Query.name == query_config.name,
            )
            result = await db.execute(stmt)
            existing = result.scalar_one_or_none()

            config_hash = calculate_hash(
                {
                    "namespace": query_config.namespace,
                    "name": query_config.name,
                    "description": query_config.description,
                    "connection_name": query_config.connectionName,
                    "operation": query_config.operation,
                    "sql": query_config.sql,
                    "input_schema": query_config.inputSchema,
                    "output_schema": query_config.outputSchema,
                    "timeout_ms": query_config.timeoutMs,
                    "max_rows": query_config.maxRows,
                }
            )

            # Resolve database connection name to ID
            db_conn_id = database_connection_ids.get(query_config.connectionName)
            if not db_conn_id:
                # Try loading from database
                db_conn = await DatabaseConnection.get_by_name(
                    db, query_config.connectionName
                )
                if db_conn:
                    db_conn_id = str(db_conn.id)
                else:
                    errors.append(
                        f"Database connection '{query_config.connectionName}' not found for query '{resource_name}'"
                    )
                    continue

            if existing:
                if should_skip_existing(existing, managed_by, config_name, config_hash, "queries", resource_name, track_change, warnings):
                    continue

                if not dry_run:
                    existing.description = query_config.description
                    existing.database_connection_id = db_conn_id
                    existing.operation = query_config.operation
                    existing.sql = query_config.sql
                    existing.input_schema = query_config.inputSchema or {}
                    existing.output_schema = query_config.outputSchema or {}
                    existing.timeout_ms = query_config.timeoutMs
                    existing.max_rows = query_config.maxRows
                    existing.is_active = True
                    existing.config_checksum = config_hash
                    existing.updated_at = datetime.utcnow()

                track_change("update", "queries", resource_name)

            else:
                if not dry_run:
                    new_query = Query(
                        namespace=query_config.namespace,
                        name=query_config.name,
                        description=query_config.description,
                        database_connection_id=db_conn_id,
                        operation=query_config.operation,
                        sql=query_config.sql,
                        input_schema=query_config.inputSchema or {},
                        output_schema=query_config.outputSchema or {},
                        timeout_ms=query_config.timeoutMs,
                        max_rows=query_config.maxRows,
                        user_id=owner_user_id,
                        is_active=True,
                        managed_by=managed_by,
                        config_name=config_name,
                        config_checksum=config_hash,
                    )
                    db.add(new_query)

                track_change("create", "queries", resource_name)

        except Exception as e:
            errors.append(
                f"Error applying query '{resource_name}': {str(e)}"
            )


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

                    # Create new version if code changed
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


async def apply_skills(
    db: AsyncSession,
    skills: list,
    dry_run: bool,
    managed_by: str,
    config_name: str,
    owner_user_id: str,
    calculate_hash: Any,
    track_change: Any,
    errors: list[str],
    warnings: list[str],
) -> None:
    """Apply skill configurations"""
    for skill_config in skills:
        try:
            stmt = select(Skill).where(
                Skill.namespace == skill_config.namespace, Skill.name == skill_config.name
            )
            result = await db.execute(stmt)
            existing = result.scalar_one_or_none()

            config_hash = calculate_hash(
                {
                    "namespace": skill_config.namespace,
                    "name": skill_config.name,
                    "description": skill_config.description,
                    "content": skill_config.content,
                }
            )

            if existing:
                if should_skip_existing(existing, managed_by, config_name, config_hash, "skills", f"{skill_config.namespace}/{skill_config.name}", track_change, warnings):
                    continue

                if not dry_run:
                    # Update skill
                    existing.description = skill_config.description
                    existing.content = skill_config.content
                    existing.is_active = True
                    existing.config_checksum = config_hash
                    existing.updated_at = datetime.utcnow()

                track_change(
                    "update", "skills", f"{skill_config.namespace}/{skill_config.name}"
                )

            else:
                if not dry_run:
                    new_skill = Skill(
                        namespace=skill_config.namespace,
                        name=skill_config.name,
                        description=skill_config.description,
                        content=skill_config.content,
                        user_id=owner_user_id,
                        is_active=True,
                        managed_by=managed_by,
                        config_name=config_name,
                        config_checksum=config_hash,
                    )
                    db.add(new_skill)

                track_change(
                    "create", "skills", f"{skill_config.namespace}/{skill_config.name}"
                )

        except Exception as e:
            errors.append(
                f"Error applying skill '{skill_config.namespace}/{skill_config.name}': {str(e)}"
            )


async def apply_components(
    db: AsyncSession,
    components: list,
    dry_run: bool,
    managed_by: str,
    config_name: str,
    owner_user_id: str,
    calculate_hash: Any,
    track_change: Any,
    errors: list[str],
    warnings: list[str],
) -> None:
    """Apply component configurations"""
    for comp_config in components:
        resource_name = f"{comp_config.namespace}/{comp_config.name}"
        try:
            stmt = select(Component).where(
                Component.namespace == comp_config.namespace,
                Component.name == comp_config.name,
            )
            result = await db.execute(stmt)
            existing = result.scalar_one_or_none()

            config_hash = calculate_hash(
                {
                    "namespace": comp_config.namespace,
                    "name": comp_config.name,
                    "title": comp_config.title,
                    "description": comp_config.description,
                    "source_code": comp_config.sourceCode,
                    "input_schema": comp_config.inputSchema or {},
                    "enabled_agents": comp_config.enabledAgents,
                    "enabled_functions": comp_config.enabledFunctions,
                    "enabled_queries": comp_config.enabledQueries,
                    "enabled_components": comp_config.enabledComponents,
                    "enabled_stores": normalize_store_references(comp_config.enabledStores) if hasattr(comp_config, 'enabledStores') else [],
                    "css_overrides": comp_config.cssOverrides,
                    "visibility": comp_config.visibility,
                }
            )

            if existing:
                if should_skip_existing(existing, managed_by, config_name, config_hash, "components", resource_name, track_change, warnings):
                    continue

                if not dry_run:
                    source_changed = existing.source_code != comp_config.sourceCode
                    existing.title = comp_config.title
                    existing.description = comp_config.description
                    existing.source_code = comp_config.sourceCode
                    existing.input_schema = comp_config.inputSchema
                    existing.enabled_agents = comp_config.enabledAgents
                    existing.enabled_functions = comp_config.enabledFunctions
                    existing.enabled_queries = comp_config.enabledQueries
                    existing.enabled_components = comp_config.enabledComponents
                    existing.enabled_stores = normalize_store_references(comp_config.enabledStores) if hasattr(comp_config, 'enabledStores') else []
                    existing.css_overrides = comp_config.cssOverrides
                    existing.visibility = comp_config.visibility
                    existing.is_active = True
                    existing.config_checksum = config_hash
                    existing.updated_at = datetime.utcnow()
                    if source_changed:
                        existing.compile_status = "pending"
                        existing.compiled_bundle = None
                        existing.source_map = None
                        existing.compile_errors = None
                        existing.version += 1

                track_change("update", "components", resource_name)

            else:
                if not dry_run:
                    new_component = Component(
                        namespace=comp_config.namespace,
                        name=comp_config.name,
                        title=comp_config.title,
                        description=comp_config.description,
                        source_code=comp_config.sourceCode,
                        input_schema=comp_config.inputSchema,
                        enabled_agents=comp_config.enabledAgents,
                        enabled_functions=comp_config.enabledFunctions,
                        enabled_queries=comp_config.enabledQueries,
                        enabled_components=comp_config.enabledComponents,
                        enabled_stores=normalize_store_references(comp_config.enabledStores) if hasattr(comp_config, 'enabledStores') else [],
                        css_overrides=comp_config.cssOverrides,
                        visibility=comp_config.visibility,
                        user_id=owner_user_id,
                        is_active=True,
                        managed_by=managed_by,
                        config_name=config_name,
                        config_checksum=config_hash,
                        compile_status="pending",
                    )
                    db.add(new_component)

                track_change("create", "components", resource_name)

        except Exception as e:
            errors.append(f"Error applying component '{resource_name}': {str(e)}")


async def apply_collections(
    db: AsyncSession,
    collections: list,
    dry_run: bool,
    managed_by: str,
    config_name: str,
    owner_user_id: str,
    calculate_hash: Any,
    track_change: Any,
    errors: list[str],
    warnings: list[str],
    collection_ids: dict[str, str],
) -> None:
    """Apply collection configurations"""
    for coll_config in collections:
        resource_name = f"{coll_config.namespace}/{coll_config.name}"
        try:
            stmt = select(Collection).where(
                Collection.namespace == coll_config.namespace,
                Collection.name == coll_config.name,
            )
            result = await db.execute(stmt)
            existing = result.scalar_one_or_none()

            config_hash = calculate_hash(
                {
                    "namespace": coll_config.namespace,
                    "name": coll_config.name,
                    "metadata_schema": coll_config.metadataSchema or {},
                    "content_filter_function": coll_config.contentFilterFunction,
                    "post_upload_function": coll_config.postUploadFunction,
                    "max_file_size_mb": coll_config.maxFileSizeMb,
                    "max_total_size_gb": coll_config.maxTotalSizeGb,
                    "is_public": coll_config.isPublic,
                    "allow_shared_files": coll_config.allowSharedFiles,
                    "allow_private_files": coll_config.allowPrivateFiles,
                }
            )

            if existing:
                if should_skip_existing(existing, managed_by, config_name, config_hash, "collections", resource_name, track_change, warnings):
                    collection_ids[resource_name] = str(existing.id)
                    continue

                if not dry_run:
                    existing.metadata_schema = coll_config.metadataSchema or {}
                    existing.content_filter_function = coll_config.contentFilterFunction
                    existing.post_upload_function = coll_config.postUploadFunction
                    existing.max_file_size_mb = coll_config.maxFileSizeMb
                    existing.max_total_size_gb = coll_config.maxTotalSizeGb
                    existing.is_public = coll_config.isPublic
                    existing.allow_shared_files = coll_config.allowSharedFiles
                    existing.allow_private_files = coll_config.allowPrivateFiles
                    existing.config_checksum = config_hash
                    existing.updated_at = datetime.utcnow()

                track_change("update", "collections", resource_name)
                collection_ids[resource_name] = str(existing.id)

            else:
                if not dry_run:
                    new_collection = Collection(
                        namespace=coll_config.namespace,
                        name=coll_config.name,
                        user_id=owner_user_id,
                        metadata_schema=coll_config.metadataSchema or {},
                        content_filter_function=coll_config.contentFilterFunction,
                        post_upload_function=coll_config.postUploadFunction,
                        max_file_size_mb=coll_config.maxFileSizeMb,
                        max_total_size_gb=coll_config.maxTotalSizeGb,
                        is_public=coll_config.isPublic,
                        allow_shared_files=coll_config.allowSharedFiles,
                        allow_private_files=coll_config.allowPrivateFiles,
                        managed_by=managed_by,
                        config_name=config_name,
                        config_checksum=config_hash,
                    )
                    db.add(new_collection)
                    await db.flush()
                    collection_ids[resource_name] = str(new_collection.id)
                else:
                    collection_ids[resource_name] = "dry-run-id"

                track_change("create", "collections", resource_name)

        except Exception as e:
            errors.append(f"Error applying collection '{resource_name}': {str(e)}")


async def apply_stores(
    db: AsyncSession,
    stores: list,
    dry_run: bool,
    managed_by: str,
    config_name: str,
    owner_user_id: str,
    calculate_hash: Any,
    track_change: Any,
    errors: list[str],
    warnings: list[str],
    store_ids: dict[str, str],
) -> None:
    """Apply store configurations"""
    for store_config in stores:
        resource_name = f"{store_config.namespace}/{store_config.name}"
        try:
            stmt = select(Store).where(
                Store.namespace == store_config.namespace,
                Store.name == store_config.name,
            )
            result = await db.execute(stmt)
            existing = result.scalar_one_or_none()

            config_hash = calculate_hash(
                {
                    "namespace": store_config.namespace,
                    "name": store_config.name,
                    "description": store_config.description,
                    "schema": store_config.schema or {},
                    "strict": store_config.strict,
                    "default_visibility": store_config.defaultVisibility,
                    "encrypted": store_config.encrypted,
                }
            )

            if existing:
                if should_skip_existing(existing, managed_by, config_name, config_hash, "stores", resource_name, track_change, warnings):
                    store_ids[resource_name] = str(existing.id)
                    continue

                if not dry_run:
                    existing.description = store_config.description
                    existing.schema = store_config.schema or {}
                    existing.strict = store_config.strict
                    existing.default_visibility = store_config.defaultVisibility
                    existing.encrypted = store_config.encrypted
                    existing.config_checksum = config_hash
                    existing.updated_at = datetime.utcnow()

                track_change("update", "stores", resource_name)
                store_ids[resource_name] = str(existing.id)

            else:
                if not dry_run:
                    new_store = Store(
                        namespace=store_config.namespace,
                        name=store_config.name,
                        description=store_config.description,
                        schema=store_config.schema or {},
                        strict=store_config.strict,
                        default_visibility=store_config.defaultVisibility,
                        encrypted=store_config.encrypted,
                        user_id=owner_user_id,
                        managed_by=managed_by,
                        config_name=config_name,
                        config_checksum=config_hash,
                    )
                    db.add(new_store)
                    await db.flush()
                    store_ids[resource_name] = str(new_store.id)
                else:
                    store_ids[resource_name] = "dry-run-id"

                track_change("create", "stores", resource_name)

        except Exception as e:
            errors.append(f"Error applying store '{resource_name}': {str(e)}")


async def apply_manifests(
    db: AsyncSession,
    manifests: list,
    dry_run: bool,
    managed_by: str,
    config_name: str,
    owner_user_id: str,
    calculate_hash: Any,
    track_change: Any,
    errors: list[str],
    warnings: list[str],
) -> None:
    """Apply manifest registration configurations"""
    for manifest_config in manifests:
        resource_name = f"{manifest_config.namespace}/{manifest_config.name}"
        try:
            stmt = select(Manifest).where(
                Manifest.namespace == manifest_config.namespace,
                Manifest.name == manifest_config.name,
            )
            result = await db.execute(stmt)
            existing = result.scalar_one_or_none()

            config_hash = calculate_hash(
                {
                    "namespace": manifest_config.namespace,
                    "name": manifest_config.name,
                    "description": manifest_config.description,
                    "required_resources": [
                        {"type": r.type, "namespace": r.namespace, "name": r.name}
                        for r in manifest_config.requiredResources
                    ],
                    "required_permissions": sorted(manifest_config.requiredPermissions),
                    "optional_permissions": sorted(manifest_config.optionalPermissions),
                    "exposed_namespaces": {
                        k: sorted(v) for k, v in sorted(manifest_config.exposedNamespaces.items())
                    },
                }
            )

            if existing:
                if should_skip_existing(existing, managed_by, config_name, config_hash, "manifests", resource_name, track_change, warnings):
                    continue

                if not dry_run:
                    existing.description = manifest_config.description
                    existing.required_resources = [
                        {"type": r.type, "namespace": r.namespace, "name": r.name}
                        for r in manifest_config.requiredResources
                    ]
                    existing.required_permissions = manifest_config.requiredPermissions
                    existing.optional_permissions = manifest_config.optionalPermissions
                    existing.exposed_namespaces = manifest_config.exposedNamespaces
                    existing.is_active = True
                    existing.config_checksum = config_hash
                    existing.updated_at = datetime.utcnow()

                track_change("update", "manifests", resource_name)

            else:
                if not dry_run:
                    new_manifest = Manifest(
                        namespace=manifest_config.namespace,
                        name=manifest_config.name,
                        description=manifest_config.description,
                        required_resources=[
                            {"type": r.type, "namespace": r.namespace, "name": r.name}
                            for r in manifest_config.requiredResources
                        ],
                        required_permissions=manifest_config.requiredPermissions,
                        optional_permissions=manifest_config.optionalPermissions,
                        exposed_namespaces=manifest_config.exposedNamespaces,
                        user_id=owner_user_id,
                        is_active=True,
                        managed_by=managed_by,
                        config_name=config_name,
                        config_checksum=config_hash,
                    )
                    db.add(new_manifest)

                track_change("create", "manifests", resource_name)

        except Exception as e:
            errors.append(f"Error applying manifest '{resource_name}': {str(e)}")


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
        try:
            existing = await db.execute(
                select(Dependency).where(Dependency.package_name == package_name)
            )
            existing_dep = existing.scalar_one_or_none()

            if existing_dep:
                # Update version if changed
                if dep_config.version and existing_dep.version != dep_config.version:
                    if not dry_run:
                        existing_dep.version = dep_config.version
                        existing_dep.installed_at = datetime.now(tz.utc)
                    track_change("update", "dependencies", package_name)
                else:
                    track_change("unchanged", "dependencies", package_name)
            else:
                if not dry_run:
                    dep = Dependency(
                        package_name=package_name,
                        version=dep_config.version,
                        installed_at=datetime.now(tz.utc),
                        installed_by=uuid_lib.UUID(owner_user_id) if owner_user_id else None,
                    )
                    db.add(dep)
                track_change("create", "dependencies", package_name)

        except Exception as e:
            errors.append(f"Error applying dependency '{package_name}': {str(e)}")
