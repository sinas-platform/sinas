"""
Integration appliers: webhooks, templates, schedules, database triggers
"""
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database_connection import DatabaseConnection
from app.models.database_trigger import DatabaseTrigger
from app.models.schedule import ScheduledJob
from app.models.template import Template
from app.models.webhook import Webhook

from app.services.config_apply.normalizers import should_skip_existing

logger = logging.getLogger(__name__)


async def apply_webhooks(
    db: AsyncSession,
    webhooks: list,
    dry_run: bool,
    managed_by: str,
    config_name: str,
    owner_user_id: str,
    calculate_hash: Any,
    track_change: Any,
    errors: list[str],
    warnings: list[str],
) -> None:
    """Apply webhook configurations"""
    for webhook_config in webhooks:
        try:
            stmt = select(Webhook).where(Webhook.path == webhook_config.path)
            result = await db.execute(stmt)
            existing = result.scalar_one_or_none()

            config_hash = calculate_hash(
                {
                    "path": webhook_config.path,
                    "function_name": webhook_config.functionName,
                    "http_method": webhook_config.httpMethod,
                    "description": webhook_config.description,
                    "requires_auth": webhook_config.requiresAuth,
                    "default_values": webhook_config.defaultValues,
                    "response_mode": webhook_config.responseMode,
                    "dedup": webhook_config.dedup.model_dump() if webhook_config.dedup else None,
                }
            )

            if existing:
                if should_skip_existing(existing, managed_by, config_name, config_hash, "webhooks", webhook_config.path, track_change, warnings):
                    continue

                if not dry_run:
                    # Parse function reference (may be "namespace/name" or just "name")
                    func_ref = webhook_config.functionName
                    if "/" in func_ref:
                        func_ns, func_name = func_ref.split("/", 1)
                    else:
                        func_ns, func_name = "default", func_ref

                    existing.function_namespace = func_ns
                    existing.function_name = func_name
                    existing.http_method = webhook_config.httpMethod
                    existing.description = webhook_config.description
                    existing.requires_auth = webhook_config.requiresAuth
                    existing.default_values = webhook_config.defaultValues
                    existing.response_mode = webhook_config.responseMode
                    existing.dedup = webhook_config.dedup.model_dump() if webhook_config.dedup else None
                    existing.is_active = True
                    existing.config_checksum = config_hash
                    existing.updated_at = datetime.utcnow()

                track_change("update", "webhooks", webhook_config.path)

            else:
                if not dry_run:
                    # Parse function reference (may be "namespace/name" or just "name")
                    func_ref = webhook_config.functionName
                    if "/" in func_ref:
                        func_ns, func_name = func_ref.split("/", 1)
                    else:
                        func_ns, func_name = "default", func_ref

                    new_webhook = Webhook(
                        path=webhook_config.path,
                        function_namespace=func_ns,
                        function_name=func_name,
                        user_id=owner_user_id,
                        http_method=webhook_config.httpMethod,
                        description=webhook_config.description,
                        requires_auth=webhook_config.requiresAuth,
                        default_values=webhook_config.defaultValues,
                        response_mode=webhook_config.responseMode,
                        dedup=webhook_config.dedup.model_dump() if webhook_config.dedup else None,
                        is_active=True,
                        managed_by=managed_by,
                        config_name=config_name,
                        config_checksum=config_hash,
                    )
                    db.add(new_webhook)

                track_change("create", "webhooks", webhook_config.path)

        except Exception as e:
            errors.append(f"Error applying webhook '{webhook_config.path}': {str(e)}")


async def apply_templates(
    db: AsyncSession,
    templates: list,
    dry_run: bool,
    managed_by: str,
    config_name: str,
    owner_user_id: str,
    calculate_hash: Any,
    track_change: Any,
    errors: list[str],
    warnings: list[str],
) -> None:
    """Apply template configurations"""
    for tmpl_config in templates:
        resource_name = f"{tmpl_config.namespace}/{tmpl_config.name}"
        try:
            stmt = select(Template).where(
                Template.namespace == tmpl_config.namespace,
                Template.name == tmpl_config.name,
            )
            result = await db.execute(stmt)
            existing = result.scalar_one_or_none()

            config_hash = calculate_hash(
                {
                    "namespace": tmpl_config.namespace,
                    "name": tmpl_config.name,
                    "description": tmpl_config.description,
                    "title": tmpl_config.title,
                    "html_content": tmpl_config.htmlContent,
                    "text_content": tmpl_config.textContent,
                    "variable_schema": tmpl_config.variableSchema or {},
                }
            )

            if existing:
                if should_skip_existing(existing, managed_by, config_name, config_hash, "templates", resource_name, track_change, warnings):
                    continue

                if not dry_run:
                    existing.description = tmpl_config.description
                    existing.title = tmpl_config.title
                    existing.html_content = tmpl_config.htmlContent
                    existing.text_content = tmpl_config.textContent
                    existing.variable_schema = tmpl_config.variableSchema or {}
                    existing.is_active = True
                    existing.config_checksum = config_hash
                    existing.updated_at = datetime.utcnow()

                track_change("update", "templates", resource_name)

            else:
                if not dry_run:
                    new_template = Template(
                        namespace=tmpl_config.namespace,
                        name=tmpl_config.name,
                        description=tmpl_config.description,
                        title=tmpl_config.title,
                        html_content=tmpl_config.htmlContent,
                        text_content=tmpl_config.textContent,
                        variable_schema=tmpl_config.variableSchema or {},
                        user_id=owner_user_id,
                        created_by=owner_user_id,
                        updated_by=owner_user_id,
                        is_active=True,
                        managed_by=managed_by,
                        config_name=config_name,
                        config_checksum=config_hash,
                    )
                    db.add(new_template)

                track_change("create", "templates", resource_name)

        except Exception as e:
            errors.append(f"Error applying template '{resource_name}': {str(e)}")


async def apply_schedules(
    db: AsyncSession,
    schedules: list,
    dry_run: bool,
    managed_by: str,
    config_name: str,
    owner_user_id: str,
    calculate_hash: Any,
    track_change: Any,
    errors: list[str],
    warnings: list[str],
) -> None:
    """Apply schedule configurations"""
    for schedule_config in schedules:
        try:
            stmt = select(ScheduledJob).where(ScheduledJob.name == schedule_config.name)
            result = await db.execute(stmt)
            existing = result.scalar_one_or_none()

            # Determine target namespace and name
            schedule_type = schedule_config.scheduleType
            if schedule_type == "agent":
                agent_ref = schedule_config.agentName or ""
                if "/" in agent_ref:
                    target_namespace, target_name = agent_ref.split("/", 1)
                else:
                    target_namespace, target_name = "default", agent_ref
            else:
                func_ref = schedule_config.functionName or ""
                if "/" in func_ref:
                    target_namespace, target_name = func_ref.split("/", 1)
                else:
                    target_namespace, target_name = "default", func_ref

            config_hash = calculate_hash(
                {
                    "name": schedule_config.name,
                    "schedule_type": schedule_type,
                    "target_namespace": target_namespace,
                    "target_name": target_name,
                    "content": schedule_config.content,
                    "cron_expression": schedule_config.cronExpression,
                    "timezone": schedule_config.timezone,
                    "input_data": schedule_config.inputData,
                    "is_active": schedule_config.isActive,
                }
            )

            if existing:
                if should_skip_existing(existing, managed_by, config_name, config_hash, "schedules", schedule_config.name, track_change, warnings):
                    continue

                if not dry_run:
                    existing.schedule_type = schedule_type
                    existing.target_namespace = target_namespace
                    existing.target_name = target_name
                    existing.content = schedule_config.content
                    existing.cron_expression = schedule_config.cronExpression
                    existing.timezone = schedule_config.timezone
                    existing.input_data = schedule_config.inputData
                    existing.is_active = schedule_config.isActive
                    existing.config_checksum = config_hash

                track_change("update", "schedules", schedule_config.name)

            else:
                if not dry_run:
                    new_schedule = ScheduledJob(
                        name=schedule_config.name,
                        schedule_type=schedule_type,
                        target_namespace=target_namespace,
                        target_name=target_name,
                        content=schedule_config.content,
                        cron_expression=schedule_config.cronExpression,
                        timezone=schedule_config.timezone,
                        input_data=schedule_config.inputData,
                        is_active=schedule_config.isActive,
                        user_id=owner_user_id,
                        managed_by=managed_by,
                        config_name=config_name,
                        config_checksum=config_hash,
                    )
                    db.add(new_schedule)

                track_change("create", "schedules", schedule_config.name)

        except Exception as e:
            errors.append(f"Error applying schedule '{schedule_config.name}': {str(e)}")


async def apply_database_triggers(
    db: AsyncSession,
    triggers: list,
    dry_run: bool,
    managed_by: str,
    config_name: str,
    owner_user_id: str,
    calculate_hash: Any,
    track_change: Any,
    errors: list[str],
    warnings: list[str],
) -> None:
    """Apply database trigger (CDC) configurations"""
    for trigger_config in triggers:
        try:
            # Resolve connection name -> id
            conn_result = await db.execute(
                select(DatabaseConnection).where(
                    DatabaseConnection.name == trigger_config.connectionName
                )
            )
            db_conn = conn_result.scalar_one_or_none()
            if not db_conn:
                errors.append(
                    f"Database trigger '{trigger_config.name}': "
                    f"connection '{trigger_config.connectionName}' not found"
                )
                continue

            # Parse function name (namespace/name format)
            func_ref = trigger_config.functionName
            if "/" in func_ref:
                func_namespace, func_name = func_ref.split("/", 1)
            else:
                func_namespace, func_name = "default", func_ref

            # Look for existing trigger
            stmt = select(DatabaseTrigger).where(DatabaseTrigger.name == trigger_config.name)
            result = await db.execute(stmt)
            existing = result.scalar_one_or_none()

            config_hash = calculate_hash(
                {
                    "name": trigger_config.name,
                    "connection_id": str(db_conn.id),
                    "schema_name": trigger_config.schemaName,
                    "table_name": trigger_config.tableName,
                    "operations": trigger_config.operations,
                    "function_namespace": func_namespace,
                    "function_name": func_name,
                    "poll_column": trigger_config.pollColumn,
                    "poll_interval_seconds": trigger_config.pollIntervalSeconds,
                    "batch_size": trigger_config.batchSize,
                    "is_active": trigger_config.isActive,
                }
            )

            if existing:
                if should_skip_existing(existing, managed_by, config_name, config_hash, "databaseTriggers", trigger_config.name, track_change, warnings):
                    continue

                if not dry_run:
                    existing.database_connection_id = db_conn.id
                    existing.schema_name = trigger_config.schemaName
                    existing.table_name = trigger_config.tableName
                    existing.operations = trigger_config.operations
                    existing.function_namespace = func_namespace
                    existing.function_name = func_name
                    existing.poll_column = trigger_config.pollColumn
                    existing.poll_interval_seconds = trigger_config.pollIntervalSeconds
                    existing.batch_size = trigger_config.batchSize
                    existing.is_active = trigger_config.isActive
                    existing.config_checksum = config_hash

                track_change("update", "databaseTriggers", trigger_config.name)

            else:
                if not dry_run:
                    new_trigger = DatabaseTrigger(
                        name=trigger_config.name,
                        database_connection_id=db_conn.id,
                        schema_name=trigger_config.schemaName,
                        table_name=trigger_config.tableName,
                        operations=trigger_config.operations,
                        function_namespace=func_namespace,
                        function_name=func_name,
                        poll_column=trigger_config.pollColumn,
                        poll_interval_seconds=trigger_config.pollIntervalSeconds,
                        batch_size=trigger_config.batchSize,
                        is_active=trigger_config.isActive,
                        user_id=owner_user_id,
                        managed_by=managed_by,
                        config_name=config_name,
                        config_checksum=config_hash,
                    )
                    db.add(new_trigger)

                track_change("create", "databaseTriggers", trigger_config.name)

        except Exception as e:
            errors.append(
                f"Error applying database trigger '{trigger_config.name}': {str(e)}"
            )
