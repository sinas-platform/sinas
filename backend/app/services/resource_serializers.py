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
    """Config form of a skill — delegated to its spec."""
    from app.services.resources.skills import SkillApplier

    return SkillApplier().spec_from_row(skill).to_config()


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
    """Config form of a component — delegated to its spec."""
    from app.services.resources.components import ComponentApplier

    return ComponentApplier().spec_from_row(comp).to_config()


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
        "publicInfo": getattr(manifest, "public_info", None) or None,
    })


def serialize_template(template) -> dict:
    """Config form of a template — delegated to its spec."""
    from app.services.resources.templates import TemplateApplier

    return TemplateApplier().spec_from_row(template).to_config()


def serialize_webhook(webhook) -> dict:
    """Config form of a webhook — delegated to its spec."""
    from app.services.resources.webhooks import WebhookApplier

    return WebhookApplier().spec_from_row(webhook).to_config()


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
    """Config form of a schedule — delegated to its spec, the one definition
    shared by the REST API, config apply, export and change history."""
    from app.services.resources.schedules import ScheduleApplier

    return ScheduleApplier().spec_from_row(schedule).to_config()


def serialize_connector(conn) -> dict:
    """Config form of a connector — delegated to its spec, whose aliases
    replace the field maps every auth field had to be added to by hand."""
    from app.services.resources.connectors import ConnectorApplier

    return ConnectorApplier().spec_from_row(conn).to_config()


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
        # Round-trips through export/import; without it an exported agent
        # re-imported at model-default effort and caching.
        "providerOverrides": agent.provider_overrides or None,
    })


def serialize_query(query, connection_name: Optional[str] = None) -> dict:
    """Config form of a query — delegated to its spec."""
    from app.services.resources.queries import QueryApplier

    return QueryApplier().spec_from_row(query, connection_name).to_config()


def serialize_database_trigger(trigger, connection_name: Optional[str] = None) -> dict:
    """Config form of a database trigger — delegated to its spec."""
    from app.services.resources.database_triggers import DatabaseTriggerApplier

    return DatabaseTriggerApplier().spec_from_row(trigger, connection_name).to_config()


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
