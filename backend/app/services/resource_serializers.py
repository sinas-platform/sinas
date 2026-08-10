"""Shared serializers for exporting Sinas resources to YAML-compatible dicts.

Used by both config_export.py (full config export) and package_service.py
(single-resource package export). One place to maintain field mappings.
"""
from typing import Any, Optional

from app.schemas.config import (
    CONNECTOR_AUTH_FIELD_MAP,
    TOKEN_RESPONSE_PATH_FIELD_MAP,
)


def _camelize_token_response_paths(paths: Any) -> Optional[dict]:
    """snake_case stored token-response paths → camelCase config keys."""
    if not isinstance(paths, dict):
        return None
    return {
        camel: paths.get(snake)
        for camel, snake in TOKEN_RESPONSE_PATH_FIELD_MAP
        if paths.get(snake) is not None
    } or None


def _remove_none_values(d: dict) -> dict:
    """Remove None values from dictionary recursively."""
    if not isinstance(d, dict):
        return d
    return {
        k: _remove_none_values(v) if isinstance(v, dict) else v
        for k, v in d.items()
        if v is not None
    }


# ─────────────────────────────────────────────────────────────
# Pure serializers (no DB access needed)
# ─────────────────────────────────────────────────────────────

def serialize_function(func) -> dict:
    return _remove_none_values({
        "namespace": func.namespace,
        "name": func.name,
        "description": func.description,
        "code": func.code,
        "inputSchema": func.input_schema,
        "outputSchema": func.output_schema,
        "icon": func.icon,
        "sharedPool": func.shared_pool if func.shared_pool else None,
        "requiresApproval": func.requires_approval if func.requires_approval else None,
        "timeout": func.timeout,
    })


def serialize_skill(skill) -> dict:
    return _remove_none_values({
        "namespace": skill.namespace,
        "name": skill.name,
        "description": skill.description,
        "content": skill.content,
    })


def serialize_collection(coll) -> dict:
    return _remove_none_values({
        "namespace": coll.namespace,
        "name": coll.name,
        "metadataSchema": coll.metadata_schema or None,
        "contentFilterFunction": coll.content_filter_function,
        "postUploadFunction": coll.post_upload_function,
        "maxFileSizeMb": coll.max_file_size_mb,
        "maxTotalSizeGb": coll.max_total_size_gb,
        "isPublic": getattr(coll, "is_public", None),
        "allowSharedFiles": coll.allow_shared_files,
        "allowPrivateFiles": coll.allow_private_files,
    })


def serialize_store(store) -> dict:
    return _remove_none_values({
        "namespace": store.namespace,
        "name": store.name,
        "description": store.description,
        "schema": store.schema or None,
        "strict": store.strict,
        "defaultVisibility": store.default_visibility,
        "encrypted": store.encrypted,
    })


def serialize_component(comp) -> dict:
    return _remove_none_values({
        "namespace": comp.namespace,
        "name": comp.name,
        "title": comp.title,
        "description": comp.description,
        "sourceCode": comp.source_code,
        "inputSchema": comp.input_schema,
        "enabledAgents": comp.enabled_agents or None,
        "enabledFunctions": comp.enabled_functions or None,
        "enabledQueries": comp.enabled_queries or None,
        "enabledComponents": comp.enabled_components or None,
        "enabledStores": comp.enabled_stores or None,
        "cssOverrides": comp.css_overrides,
        "visibility": comp.visibility,
    })


def serialize_manifest(manifest) -> dict:
    return _remove_none_values({
        "namespace": manifest.namespace,
        "name": manifest.name,
        "description": manifest.description,
        "requiredResources": manifest.required_resources or None,
        "requiredPermissions": manifest.required_permissions or None,
        "optionalPermissions": manifest.optional_permissions or None,
        "exposedNamespaces": manifest.exposed_namespaces or None,
        "storeDependencies": getattr(manifest, "store_dependencies", None) or None,
    })


def serialize_template(template) -> dict:
    return _remove_none_values({
        "namespace": template.namespace,
        "name": template.name,
        "description": template.description,
        "title": template.title,
        "htmlContent": template.html_content,
        "textContent": template.text_content,
        "variableSchema": template.variable_schema if template.variable_schema else None,
    })


def serialize_webhook(webhook) -> dict:
    target_type = getattr(webhook, "target_type", "function") or "function"
    return _remove_none_values({
        "path": webhook.path,
        # Omitted for function targets so legacy exports stay unchanged
        "targetType": target_type if target_type != "function" else None,
        "functionName": f"{webhook.function_namespace}/{webhook.function_name}"
        if target_type == "function"
        else None,
        "agentName": f"{webhook.agent_namespace}/{webhook.agent_name}"
        if target_type == "agent"
        else None,
        "pipelineName": f"{webhook.pipeline_namespace or 'default'}/{webhook.pipeline_name}"
        if target_type == "pipeline"
        else None,
        "messageTemplate": webhook.message_template if target_type == "agent" else None,
        "sessionKeyTemplate": webhook.session_key_template if target_type == "agent" else None,
        "httpMethod": webhook.http_method,
        "requiresAuth": webhook.requires_auth,
        "description": webhook.description,
        "defaultValues": webhook.default_values or None,
        "responseMode": getattr(webhook, "response_mode", None),
        "dedup": _serialize_dedup(getattr(webhook, "dedup", None)),
    })


def _serialize_dedup(dedup: Optional[dict]) -> Optional[dict]:
    """Export a stored dedup blob in the config schema's camelCase shape.

    Storage is snake_case (`ttl_seconds`); the config schema expects
    `ttlSeconds`. Exporting the raw blob emitted the snake_case key, which
    WebhookDedupConfig then ignored on re-apply — silently resetting the TTL to
    its default on a no-op round-trip. Older rows may still hold `ttlSeconds`,
    so accept either on the way out.
    """
    if not dedup:
        return None
    ttl = dedup.get("ttl_seconds")
    if not isinstance(ttl, int) or isinstance(ttl, bool):
        ttl = dedup.get("ttlSeconds")
    out: dict[str, Any] = {"key": dedup.get("key")}
    if isinstance(ttl, int) and not isinstance(ttl, bool):
        out["ttlSeconds"] = ttl
    return out


def serialize_schedule(schedule) -> dict:
    return _remove_none_values({
        "name": schedule.name,
        "scheduleType": schedule.schedule_type,
        "functionName": f"{schedule.target_namespace}/{schedule.target_name}"
        if schedule.schedule_type == "function"
        else None,
        "agentName": f"{schedule.target_namespace}/{schedule.target_name}"
        if schedule.schedule_type == "agent"
        else None,
        "pipelineName": f"{schedule.target_namespace}/{schedule.target_name}"
        if schedule.schedule_type == "pipeline"
        else None,
        "content": schedule.content,
        "cronExpression": schedule.cron_expression,
        "isActive": schedule.is_active,
        "timezone": schedule.timezone,
        "inputData": schedule.input_data or None,
    })


def serialize_connector(conn) -> dict:
    auth = conn.auth or {}
    retry = conn.retry or {}
    operations = []
    for op in (conn.operations or []):
        op_dict = {
            "name": op.get("name"),
            "method": op.get("method"),
            "path": op.get("path"),
            "description": op.get("description"),
            "parameters": op.get("parameters"),
            "requestBodyMapping": op.get("request_body_mapping", "json"),
            "responseMapping": op.get("response_mapping", "json"),
        }
        operations.append(_remove_none_values(op_dict))

    return _remove_none_values({
        "namespace": conn.namespace,
        "name": conn.name,
        "description": conn.description,
        "baseUrl": conn.base_url,
        # Map snake_case stored keys → camelCase config keys via the single field map.
        "auth": _remove_none_values({
            **{camel: auth.get(snake) for camel, snake in CONNECTOR_AUTH_FIELD_MAP},
            "type": auth.get("type", "none"),  # type always present in export
            # Nested object: its inner keys need their own camelization.
            "tokenResponsePaths": _camelize_token_response_paths(
                auth.get("token_response_paths")
            ),
        }),
        "headers": conn.headers if conn.headers else None,
        "retry": _remove_none_values({
            "maxAttempts": retry.get("max_attempts", 1),
            "backoff": retry.get("backoff", "none"),
        }),
        "timeoutSeconds": conn.timeout_seconds,
        "operations": operations,
    })


# ─────────────────────────────────────────────────────────────
# Serializers that need resolved foreign keys (provider name,
# connection name). Caller passes the resolved name.
# ─────────────────────────────────────────────────────────────

def serialize_agent(agent, provider_name: Optional[str] = None) -> dict:
    return _remove_none_values({
        "namespace": agent.namespace,
        "name": agent.name,
        "description": agent.description,
        "model": agent.model,
        "llmProviderName": provider_name,
        "temperature": agent.temperature,
        "maxTokens": agent.max_tokens,
        "systemPrompt": agent.system_prompt,
        "inputSchema": agent.input_schema if agent.input_schema else None,
        "outputSchema": agent.output_schema if agent.output_schema else None,
        "initialMessages": agent.initial_messages or None,
        "enabledFunctions": agent.enabled_functions or None,
        "functionParameters": agent.function_parameters or None,
        "statusTemplates": agent.status_templates or None,
        "enabledAgents": agent.enabled_agents or None,
        "enabledSkills": agent.enabled_skills or None,
        "enabledStores": agent.enabled_stores or None,
        "enabledQueries": agent.enabled_queries or None,
        "queryParameters": agent.query_parameters or None,
        "enabledCollections": agent.enabled_collections or None,
        "enabledComponents": agent.enabled_components or None,
        "enabledConnectors": agent.enabled_connectors or None,
        "enabledPipelines": agent.enabled_pipelines or None,
        "hooks": agent.hooks or None,
        "icon": agent.icon,
        "isDefault": agent.is_default if agent.is_default else None,
        "defaultJobTimeout": agent.default_job_timeout,
        "defaultKeepAlive": agent.default_keep_alive if agent.default_keep_alive else None,
        "systemTools": agent.system_tools if agent.system_tools else None,
    })


def serialize_query(query, connection_name: Optional[str] = None) -> dict:
    return _remove_none_values({
        "namespace": query.namespace,
        "name": query.name,
        "description": query.description,
        "connectionName": connection_name,
        "operation": query.operation,
        "sql": query.sql,
        "inputSchema": query.input_schema,
        "outputSchema": query.output_schema,
        "timeoutMs": query.timeout_ms,
        "maxRows": query.max_rows,
    })


def serialize_database_trigger(trigger, connection_name: Optional[str] = None) -> dict:
    return _remove_none_values({
        "name": trigger.name,
        "connectionName": connection_name,
        "schemaName": trigger.schema_name,
        "tableName": trigger.table_name,
        "operations": trigger.operations,
        "targetType": trigger.target_type if trigger.target_type != "function" else None,
        "functionName": f"{trigger.function_namespace}/{trigger.function_name}"
        if trigger.function_name
        else None,
        "pipelineName": f"{trigger.pipeline_namespace or 'default'}/{trigger.pipeline_name}"
        if trigger.pipeline_name
        else None,
        "pollColumn": trigger.poll_column,
        "pollIntervalSeconds": trigger.poll_interval_seconds,
        "batchSize": trigger.batch_size,
        "isActive": trigger.is_active,
    })


def serialize_pipeline(pipeline) -> dict:
    """Export a pipeline. cursor_value / error_message / failure counters are
    runtime state, not config — deliberately not exported. Steps/perUser are
    stored verbatim (camelCase, `.$` keys intact) and pass straight through."""
    out = {
        "namespace": pipeline.namespace,
        "name": pipeline.name,
        "description": pipeline.description,
        "inputSchema": pipeline.input_schema or None,
        "steps": pipeline.steps,
        "perUser": pipeline.per_user,
        "asTool": pipeline.as_tool or None,
        "toolDescription": pipeline.tool_description,
        "syncTimeoutSeconds": pipeline.sync_timeout_seconds if pipeline.sync_timeout_seconds != 120 else None,
        "concurrency": pipeline.concurrency,
        "disableAfterFailures": pipeline.disable_after_failures,
        "isActive": pipeline.is_active,
    }
    mapping = pipeline.output_mapping or {}
    if "output.$" in mapping:
        out["output.$"] = mapping["output.$"]
    elif "output" in mapping:
        out["output"] = mapping["output"]
    return _remove_none_values(out)
