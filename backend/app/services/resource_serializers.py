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
    """Config form of a function — delegated to its spec."""
    from app.services.resources.functions import FunctionApplier

    return FunctionApplier().spec_from_row(func).to_config()


def serialize_skill(skill) -> dict:
    """Config form of a skill — delegated to its spec."""
    from app.services.resources.skills import SkillApplier

    return SkillApplier().spec_from_row(skill).to_config()


def serialize_collection(coll) -> dict:
    """Config form of a collection — delegated to its spec."""
    from app.services.resources.collections import CollectionApplier

    return CollectionApplier().spec_from_row(coll).to_config()


def serialize_store(store) -> dict:
    """Config form of a store — delegated to its spec."""
    from app.services.resources.stores import StoreApplier

    return StoreApplier().spec_from_row(store).to_config()


def serialize_component(comp) -> dict:
    """Config form of a component — delegated to its spec."""
    from app.services.resources.components import ComponentApplier

    return ComponentApplier().spec_from_row(comp).to_config()


def serialize_manifest(manifest) -> dict:
    """Config form of a manifest — delegated to its spec."""
    from app.services.resources.manifests import ManifestApplier

    return ManifestApplier().spec_from_row(manifest).to_config()


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
    """Config form of an agent — delegated to its spec."""
    from app.services.resources.agents import AgentApplier

    return AgentApplier().spec_from_row(agent, provider_name).to_config()


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
