"""
Integration appliers: webhooks, templates, schedules, database triggers
"""
import logging
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database_connection import DatabaseConnection
from app.models.database_trigger import DatabaseTrigger
from app.models.schedule import ScheduledJob
from app.models.template import Template
from app.models.webhook import Webhook

from app.services.config_apply.normalizers import should_skip_existing

logger = logging.getLogger(__name__)



def _dedup_storage(dedup) -> Optional[dict]:
    """Canonical storage shape for a webhook dedup block.

    The config schema field is `ttlSeconds`, but the stored blob is read by
    dedup_service (and written by the REST schema) as `ttl_seconds`. Dumping the
    config model verbatim stored the camelCase key, which the consumer never
    read — every config-managed webhook silently ran on the default TTL. Emit
    the snake_case shape so both write paths agree.
    """
    if not dedup:
        return None
    return {"key": dedup.key, "ttl_seconds": dedup.ttlSeconds}


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

            target_type = getattr(webhook_config, "targetType", "function")

            # Parse target references (may be "namespace/name" or just "name")
            func_ns, func_name = "default", None
            agent_ns, agent_name = None, None
            pipeline_ns, pipeline_name = None, None
            if target_type == "agent":
                agent_ref = webhook_config.agentName or ""
                if "/" in agent_ref:
                    agent_ns, agent_name = agent_ref.split("/", 1)
                else:
                    agent_ns, agent_name = "default", agent_ref
            elif target_type == "pipeline":
                pipeline_ref = getattr(webhook_config, "pipelineName", None) or ""
                if "/" in pipeline_ref:
                    pipeline_ns, pipeline_name = pipeline_ref.split("/", 1)
                else:
                    pipeline_ns, pipeline_name = "default", pipeline_ref
            else:
                func_ref = webhook_config.functionName or ""
                if "/" in func_ref:
                    func_ns, func_name = func_ref.split("/", 1)
                else:
                    func_ns, func_name = "default", func_ref

            config_hash = calculate_hash(
                {
                    "path": webhook_config.path,
                    "target_type": target_type,
                    "function_name": webhook_config.functionName,
                    "agent_name": webhook_config.agentName,
                    "pipeline_name": getattr(webhook_config, "pipelineName", None),
                    "message_template": webhook_config.messageTemplate,
                    "session_key_template": webhook_config.sessionKeyTemplate,
                    "http_method": webhook_config.httpMethod,
                    "description": webhook_config.description,
                    "requires_auth": webhook_config.requiresAuth,
                    "default_values": webhook_config.defaultValues,
                    "response_mode": webhook_config.responseMode,
                    "dedup": _dedup_storage(webhook_config.dedup),
                }
            )

            if existing:
                if should_skip_existing(existing, managed_by, config_name, config_hash, "webhooks", webhook_config.path, track_change, warnings):
                    continue

                if not dry_run:
                    existing.target_type = target_type
                    existing.function_namespace = func_ns
                    existing.function_name = func_name
                    existing.agent_namespace = agent_ns
                    existing.agent_name = agent_name
                    existing.pipeline_namespace = pipeline_ns
                    existing.pipeline_name = pipeline_name
                    existing.message_template = webhook_config.messageTemplate
                    existing.session_key_template = webhook_config.sessionKeyTemplate
                    existing.http_method = webhook_config.httpMethod
                    existing.description = webhook_config.description
                    existing.requires_auth = webhook_config.requiresAuth
                    existing.default_values = webhook_config.defaultValues
                    existing.response_mode = webhook_config.responseMode
                    existing.dedup = _dedup_storage(webhook_config.dedup)
                    # Deliberately NOT re-enabling: is_active is operator state,
                    # not config state (WebhookConfig has no isActive field), so
                    # a re-apply must not silently re-arm a webhook someone
                    # disabled. This matters because adding fields to the
                    # config_hash input changes every existing webhook's
                    # checksum, so the first apply after such a change updates
                    # them all even when the YAML is byte-identical.
                    existing.config_checksum = config_hash
                    existing.updated_at = datetime.utcnow()

                track_change("update", "webhooks", webhook_config.path)

            else:
                if not dry_run:
                    new_webhook = Webhook(
                        path=webhook_config.path,
                        target_type=target_type,
                        function_namespace=func_ns,
                        function_name=func_name,
                        agent_namespace=agent_ns,
                        agent_name=agent_name,
                        pipeline_namespace=pipeline_ns,
                        pipeline_name=pipeline_name,
                        message_template=webhook_config.messageTemplate,
                        session_key_template=webhook_config.sessionKeyTemplate,
                        user_id=owner_user_id,
                        http_method=webhook_config.httpMethod,
                        description=webhook_config.description,
                        requires_auth=webhook_config.requiresAuth,
                        default_values=webhook_config.defaultValues,
                        response_mode=webhook_config.responseMode,
                        dedup=_dedup_storage(webhook_config.dedup),
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
    notify_scheduler: Any = None,
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
            elif schedule_type == "pipeline":
                pipeline_ref = schedule_config.pipelineName or ""
                if "/" in pipeline_ref:
                    target_namespace, target_name = pipeline_ref.split("/", 1)
                else:
                    target_namespace, target_name = "default", pipeline_ref
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
                    if notify_scheduler:
                        # Without this the running scheduler never learns about
                        # config-applied schedules and keeps the old cron (or
                        # none) until it restarts.
                        notify_scheduler("update", str(existing.id))

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
                    if notify_scheduler:
                        # flush to obtain the generated id; the event itself is
                        # published only after the transaction commits.
                        await db.flush()
                        notify_scheduler("create", str(new_schedule.id))

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

            # Parse target refs (namespace/name format)
            target_type = getattr(trigger_config, "targetType", "function")
            func_ref = trigger_config.functionName or ""
            if "/" in func_ref:
                func_namespace, func_name = func_ref.split("/", 1)
            elif func_ref:
                func_namespace, func_name = "default", func_ref
            else:
                func_namespace, func_name = "default", None
            pipeline_ref = getattr(trigger_config, "pipelineName", None) or ""
            if "/" in pipeline_ref:
                pipeline_namespace, pipeline_name = pipeline_ref.split("/", 1)
            elif pipeline_ref:
                pipeline_namespace, pipeline_name = "default", pipeline_ref
            else:
                pipeline_namespace, pipeline_name = None, None

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
                    "target_type": target_type,
                    "function_namespace": func_namespace,
                    "function_name": func_name,
                    "pipeline_namespace": pipeline_namespace,
                    "pipeline_name": pipeline_name,
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
                    existing.target_type = target_type
                    existing.function_namespace = func_namespace
                    existing.function_name = func_name
                    existing.pipeline_namespace = pipeline_namespace
                    existing.pipeline_name = pipeline_name
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
                        target_type=target_type,
                        function_namespace=func_namespace,
                        function_name=func_name,
                        pipeline_namespace=pipeline_namespace,
                        pipeline_name=pipeline_name,
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
