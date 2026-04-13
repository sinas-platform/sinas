"""Shared serializers for exporting Sinas resources to YAML-compatible dicts.

Used by both config_export.py (full config export) and package_service.py
(single-resource package export). One place to maintain field mappings.
"""
from typing import Any, Optional


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
    return _remove_none_values({
        "path": webhook.path,
        "functionName": f"{webhook.function_namespace}/{webhook.function_name}",
        "httpMethod": webhook.http_method,
        "requiresAuth": webhook.requires_auth,
        "description": webhook.description,
        "defaultValues": webhook.default_values or None,
        "responseMode": getattr(webhook, "response_mode", None),
        "dedup": getattr(webhook, "dedup", None) or None,
    })


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
        "auth": _remove_none_values({
            "type": auth.get("type", "none"),
            "secret": auth.get("secret"),
            "header": auth.get("header"),
            "position": auth.get("position"),
            "paramName": auth.get("param_name"),
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
        "functionName": f"{trigger.function_namespace}/{trigger.function_name}",
        "pollColumn": trigger.poll_column,
        "pollIntervalSeconds": trigger.poll_interval_seconds,
        "batchSize": trigger.batch_size,
        "isActive": trigger.is_active,
    })
