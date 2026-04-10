"""Sinas config introspection tools for agents.

Exposes read-only inspection of the current Sinas configuration as LLM tools
for agents that have opted in via `system_tools: ["configIntrospection"]`.

Progressive disclosure pattern:
  1. sinas_config_inspect  → resource type counts
  2. sinas_config_list     → names + descriptions for a type
  3. sinas_config_get      → full detail of one resource

These never modify anything — they're purely read tools.
"""
import logging
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Resource type → (model class import path, name field, description field, extra summary fields)
_RESOURCE_TYPES = {
    "agents": "app.models.agent:Agent",
    "functions": "app.models.function:Function",
    "queries": "app.models.query:Query",
    "skills": "app.models.skill:Skill",
    "collections": "app.models.file:Collection",
    "stores": "app.models.store:Store",
    "components": "app.models.component:Component",
    "manifests": "app.models.manifest:Manifest",
    "templates": "app.models.template:Template",
    "webhooks": "app.models.webhook:Webhook",
    "schedules": "app.models.schedule:ScheduledJob",
    "databaseTriggers": "app.models.database_trigger:DatabaseTrigger",
    "connectors": "app.models.connector:Connector",
    "packages": "app.models.package:Package",
}


def _get_model_class(resource_type: str):
    """Lazy-import a model class by type name."""
    path = _RESOURCE_TYPES.get(resource_type)
    if not path:
        return None
    module_path, class_name = path.rsplit(":", 1)
    import importlib
    mod = importlib.import_module(module_path)
    return getattr(mod, class_name)


# ─────────────────────────────────────────────────────────────
# Tool definitions
# ─────────────────────────────────────────────────────────────

_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "sinas_config_inspect",
            "description": (
                "Get an overview of the current Sinas configuration. Returns "
                "resource type counts: how many agents, functions, queries, "
                "collections, etc. exist. Use this as a starting point before "
                "drilling into specific resource types."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
            "_metadata": {"system_tool": "configIntrospection"},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sinas_config_list",
            "description": (
                "List resources of a specific type. Returns namespace, name, "
                "and description for each — NOT the full configuration. Use "
                "this to find resources, then call sinas_config_get to read "
                "the full detail of a specific one. Supported types: agents, "
                "functions, queries, skills, collections, stores, components, "
                "manifests, templates, webhooks, schedules, databaseTriggers, "
                "connectors, packages."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "description": "Resource type to list.",
                        "enum": list(_RESOURCE_TYPES.keys()),
                    },
                    "namespace": {
                        "type": "string",
                        "description": "Optional: filter by namespace.",
                    },
                },
                "required": ["type"],
            },
            "_metadata": {"system_tool": "configIntrospection"},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sinas_config_get",
            "description": (
                "Get the full configuration of a specific resource. Returns "
                "all fields including system prompt, SQL, code, schemas, etc. "
                "Use sinas_config_list first to find the namespace/name."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "description": "Resource type.",
                        "enum": list(_RESOURCE_TYPES.keys()),
                    },
                    "namespace": {
                        "type": "string",
                        "description": "Resource namespace.",
                    },
                    "name": {
                        "type": "string",
                        "description": "Resource name.",
                    },
                },
                "required": ["type", "namespace", "name"],
            },
            "_metadata": {"system_tool": "configIntrospection"},
        },
    },
]


def get_config_tool_definitions() -> list[dict[str, Any]]:
    """Return the list of config introspection tool definitions."""
    return [t.copy() for t in _TOOL_DEFINITIONS]


CONFIG_TOOL_NAMES = {t["function"]["name"] for t in _TOOL_DEFINITIONS}


def is_config_tool(tool_name: str) -> bool:
    """Check if a tool name is a Sinas config introspection tool."""
    return tool_name in CONFIG_TOOL_NAMES


# ─────────────────────────────────────────────────────────────
# Dispatch
# ─────────────────────────────────────────────────────────────

async def execute_config_tool(
    db: AsyncSession,
    tool_name: str,
    arguments: dict[str, Any],
    agent_system_tools: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Dispatch a config introspection tool call."""
    if "configIntrospection" not in (agent_system_tools or []):
        return {
            "error": "capability_not_enabled",
            "detail": (
                "This agent does not have 'configIntrospection' in its "
                "systemTools list. An admin must enable it on the agent."
            ),
        }

    try:
        if tool_name == "sinas_config_inspect":
            return await _inspect(db)
        if tool_name == "sinas_config_list":
            return await _list(db, arguments)
        if tool_name == "sinas_config_get":
            return await _get(db, arguments)
        return {"error": "unknown_tool", "detail": f"Unknown config tool: {tool_name}"}
    except Exception as e:
        logger.error(f"Config tool {tool_name} failed: {e}", exc_info=True)
        return {"error": "internal_error", "detail": str(e)}


# ─────────────────────────────────────────────────────────────
# Tool implementations
# ─────────────────────────────────────────────────────────────

async def _inspect(db: AsyncSession) -> dict[str, Any]:
    """Overview: resource type counts."""
    counts = {}
    for type_name in _RESOURCE_TYPES:
        model = _get_model_class(type_name)
        if not model:
            continue
        try:
            result = await db.execute(select(func.count()).select_from(model))
            count = result.scalar() or 0
            if count > 0:
                counts[type_name] = count
        except Exception:
            # Some models might not have a simple count (e.g. missing table)
            pass

    return {"resource_counts": counts, "total": sum(counts.values())}


async def _list(db: AsyncSession, arguments: dict[str, Any]) -> dict[str, Any]:
    """List resources of a type — names and descriptions only."""
    resource_type = arguments.get("type", "")
    namespace_filter = arguments.get("namespace")

    model = _get_model_class(resource_type)
    if not model:
        return {"error": "invalid_type", "detail": f"Unknown resource type: {resource_type}"}

    query = select(model)

    # Apply namespace filter if the model has a namespace column
    if namespace_filter and hasattr(model, "namespace"):
        query = query.where(model.namespace == namespace_filter)

    # Filter active-only for models that have is_active
    if hasattr(model, "is_active"):
        query = query.where(model.is_active == True)

    # Order by namespace + name where available
    if hasattr(model, "namespace") and hasattr(model, "name"):
        query = query.order_by(model.namespace, model.name)
    elif hasattr(model, "name"):
        query = query.order_by(model.name)

    query = query.limit(200)

    result = await db.execute(query)
    resources = result.scalars().all()

    items = []
    for r in resources:
        item: dict[str, Any] = {}
        if hasattr(r, "namespace"):
            item["namespace"] = r.namespace
        if hasattr(r, "name"):
            item["name"] = r.name
        if hasattr(r, "description"):
            item["description"] = r.description
        if hasattr(r, "version"):
            item["version"] = r.version
        if hasattr(r, "is_active"):
            item["is_active"] = r.is_active
        if hasattr(r, "managed_by") and r.managed_by:
            item["managed_by"] = r.managed_by
        items.append(item)

    return {"type": resource_type, "items": items, "count": len(items)}


async def _get(db: AsyncSession, arguments: dict[str, Any]) -> dict[str, Any]:
    """Get full detail of a specific resource."""
    resource_type = arguments.get("type", "")
    namespace = arguments.get("namespace", "")
    name = arguments.get("name", "")

    if not namespace or not name:
        return {"error": "missing_fields", "detail": "Both 'namespace' and 'name' are required"}

    model = _get_model_class(resource_type)
    if not model:
        return {"error": "invalid_type", "detail": f"Unknown resource type: {resource_type}"}

    # Look up by namespace + name (most models have this)
    if hasattr(model, "namespace") and hasattr(model, "name"):
        query = select(model).where(model.namespace == namespace, model.name == name)
    elif hasattr(model, "name"):
        query = select(model).where(model.name == name)
    else:
        return {"error": "unsupported", "detail": f"Resource type '{resource_type}' does not support namespace/name lookup"}

    result = await db.execute(query)
    resource = result.scalar_one_or_none()
    if not resource:
        return {"error": "not_found", "detail": f"{resource_type} '{namespace}/{name}' not found"}

    # Serialize all non-internal columns
    data: dict[str, Any] = {}
    for col in resource.__table__.columns:
        col_name = col.name
        if col_name.startswith("_"):
            continue
        val = getattr(resource, col_name, None)
        if val is None:
            continue
        # Convert non-serializable types
        if hasattr(val, "isoformat"):
            val = val.isoformat()
        elif hasattr(val, "hex"):
            val = str(val)
        data[col_name] = val

    return {"type": resource_type, "namespace": namespace, "name": name, "data": data}
