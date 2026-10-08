"""Artifacts: components an agent writes during a chat.

Opt-in per agent via `system_tools: ["artifacts"]`. An artifact is an
ordinary component (an HTML page with the `sinas` client) in the `artifacts`
namespace, owned by the chat's user and written through ComponentApplier —
so it is validated, recorded in change history, editable and shareable like
any other component. It shows in the chat the moment it is made.

What an artifact may reach is bounded by the agent: it can only declare
queries, functions and stores the agent itself has enabled (wildcards as in
tool discovery; a store no more writable than the agent has it), checked on
the whole resulting set on every create and update. Viewers' own permissions
cap it further, as for every component.
"""

from __future__ import annotations

import logging
import re
import secrets
from typing import Any, Optional

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import check_permission
from app.models.component import Component
from app.services.content_tokens import generate_component_render_token
from app.services.resources import ApplierError, ApplyContext
from app.services.resources.components import ComponentApplier

logger = logging.getLogger(__name__)

ARTIFACT_NAMESPACE = "artifacts"
_META = {"system_tool": "artifacts"}

_RESOURCES_SCHEMA = {
    "queries": {
        "type": "array", "items": {"type": "string"},
        "description": "Queries (namespace/name) the page may run with sinas.query(); only ones you have enabled.",
    },
    "functions": {
        "type": "array", "items": {"type": "string"},
        "description": "Functions (namespace/name) the page may run with sinas.run(); only ones you have enabled.",
    },
    "stores": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "store": {"type": "string", "description": "namespace/name"},
                "access": {"type": "string", "enum": ["readonly", "readwrite"]},
            },
            "required": ["store"],
        },
        "description": "Stores the page may use with sinas.store(); only ones you have enabled.",
    },
}

_HTML_HELP = (
    "The page body: HTML with <style> and <script>. Keep it self-contained and plain. "
    "Scripts get window.sinas: sinas.input (the inputs below), await sinas.query('ns/name', input) "
    "-> {data, ...}, await sinas.run('ns/name', input), sinas.store('ns/name').get/set/delete/list(). "
    "Set text with textContent (or escape values) rather than interpolating data into HTML."
)

_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "create_artifact",
            "description": (
                "Create an artifact: a small interactive page (table, chart, form, report) shown to the "
                "user right here in the chat, which they can keep, edit and share. Use it when a visual "
                "or interactive result serves the user better than text."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short title"},
                    "html": {"type": "string", "description": _HTML_HELP},
                    "input": {
                        "type": "object",
                        "description": "Values for sinas.input when shown now (e.g. the data to display).",
                    },
                    "description": {"type": "string", "description": "What the page is for (optional)"},
                    **_RESOURCES_SCHEMA,
                },
                "required": ["title", "html"],
            },
        },
        "_metadata": _META,
    },
    {
        "type": "function",
        "function": {
            "name": "update_artifact",
            "description": (
                "Change an artifact you created in this conversation (by the name create_artifact "
                "returned) and show the new version. Fields you leave out stay as they are."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The artifact's name"},
                    "html": {"type": "string", "description": _HTML_HELP},
                    "title": {"type": "string"},
                    "input": {"type": "object", "description": "Values for sinas.input when shown now."},
                    **_RESOURCES_SCHEMA,
                },
                "required": ["name"],
            },
        },
        "_metadata": _META,
    },
]

ARTIFACT_TOOL_NAMES = {t["function"]["name"] for t in _TOOL_DEFINITIONS}


def get_artifact_tool_definitions() -> list[dict[str, Any]]:
    return [dict(t) for t in _TOOL_DEFINITIONS]


def is_artifact_tool(tool_name: str) -> bool:
    return tool_name in ARTIFACT_TOOL_NAMES


def _slug(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")[:40].strip("-")
    return f"{slug or 'artifact'}-{secrets.token_hex(3)}"


def _declared_resources(arguments: dict[str, Any]) -> dict[str, Any]:
    """The resource fields a call sets (left out: unchanged on update)."""
    declared: dict[str, Any] = {}
    if "queries" in arguments:
        declared["enabled_queries"] = list(arguments["queries"] or [])
    if "functions" in arguments:
        declared["enabled_functions"] = list(arguments["functions"] or [])
    if "stores" in arguments:
        declared["enabled_stores"] = [
            {
                "store": entry.get("store") if isinstance(entry, dict) else entry,
                "access": (entry.get("access") if isinstance(entry, dict) else None) or "readonly",
            }
            for entry in arguments["stores"] or []
        ]
    return declared


async def _check_within_agent(db: AsyncSession, spec, agent) -> None:
    """Everything the artifact will be able to reach — the whole resulting
    set, not just what this call names (an update keeps what it leaves out)
    — must be within what the calling agent may reach, under the same
    conditions. Wildcard grants ("sales/*") match as in tool discovery.

    A page calls its resources directly, outside the agent's tool loop, so
    what that loop enforces can't follow: a query or function the agent has
    parameters for (pre-filled or locked inputs, e.g. an account id) and a
    function that needs approval are refused rather than run unguarded."""
    from app.models.function import Function
    from app.services.resource_resolver import matches_ref_pattern

    refs = [*spec.enabled_queries, *spec.enabled_functions, *(e.store for e in spec.enabled_stores)]
    for ref in refs:
        if "*" in ref:
            raise PermissionError(f"Name {ref} exactly: an artifact can't declare a wildcard")
    query_params = agent.query_parameters or {}
    function_params = agent.function_parameters or {}
    for ref in spec.enabled_queries:
        if not matches_ref_pattern(ref, agent.enabled_queries or []):
            raise PermissionError(f"Query {ref} is not enabled for this agent")
        if ref in query_params:
            raise PermissionError(
                f"Query {ref} has parameters set by this agent, which a page can't enforce"
            )
    for ref in spec.enabled_functions:
        if not matches_ref_pattern(ref, agent.enabled_functions or []):
            raise PermissionError(f"Function {ref} is not enabled for this agent")
        if ref in function_params:
            raise PermissionError(
                f"Function {ref} has parameters set by this agent, which a page can't enforce"
            )
        namespace, _, name = ref.partition("/")
        function = await Function.get_by_name(db, namespace, name)
        if function is not None and function.requires_approval:
            raise PermissionError(f"Function {ref} needs approval, which a page can't ask for")
    if spec.enabled_agents or spec.enabled_components:
        raise PermissionError("Artifacts can't declare agents or components")
    agent_stores = [e for e in (agent.enabled_stores or []) if isinstance(e, dict)]
    for entry in spec.enabled_stores:
        matching = [e for e in agent_stores if matches_ref_pattern(entry.store, [e])]
        if not matching:
            raise PermissionError(f"Store {entry.store} is not enabled for this agent")
        writable = any(e.get("access") == "readwrite" for e in matching)
        if entry.access == "readwrite" and not writable:
            raise PermissionError(f"Store {entry.store} is read-only for this agent")


def _shown(component: Component, user_id: str, input_values: Optional[dict]) -> dict[str, Any]:
    """The chat block that renders the artifact (same as show_component)."""
    ref = f"{component.namespace}/{component.name}"
    return {
        "type": "component",
        "namespace": component.namespace,
        "name": component.name,
        "title": component.title or ref,
        "input": input_values or {},
        "render_token": generate_component_render_token(component.namespace, component.name, user_id),
        "display": (
            f"[USER SEES ARTIFACT '{component.title or ref}' HERE. Its name is '{component.name}': "
            "use update_artifact with that name to change it. The user can share it from the "
            f"console (Components → {ref} → Share).]"
        ),
    }


async def execute_artifact_tool(
    db: AsyncSession,
    tool_name: str,
    arguments: dict[str, Any],
    user_id: str,
    permissions: dict[str, bool],
    agent,
) -> dict[str, Any]:
    """Dispatch an artifact tool call. Errors come back as {"error", "detail"}
    so the model can correct itself."""
    from app.services.system_tool_helpers import has_system_tool

    if agent is None or not has_system_tool(agent.system_tools or [], "artifacts"):
        return {
            "error": "capability_not_enabled",
            "detail": "This agent does not have 'artifacts' in its systemTools list.",
        }
    applier = ComponentApplier()
    ctx = ApplyContext(db=db, origin="api", actor_user_id=user_id, owner_user_id=user_id)
    try:
        if tool_name == "create_artifact":
            if not check_permission(permissions, "sinas.components.create:own"):
                raise PermissionError("The user may not create components")
            spec = applier.spec_model.model_validate({
                "namespace": ARTIFACT_NAMESPACE,
                "name": _slug(arguments.get("title", "")),
                "title": arguments.get("title"),
                "description": arguments.get("description"),
                "source_code": arguments.get("html", ""),
                **_declared_resources(arguments),
            })
            await _check_within_agent(db, spec, agent)
            async with db.begin_nested():
                result = await applier.apply(spec, ctx, must_create=True)
            # Tool calls run in their own session: commit, or it's gone.
            await db.commit()
            return _shown(result.obj, user_id, arguments.get("input"))

        if tool_name == "update_artifact":
            name = arguments.get("name") or ""
            component = await Component.get_by_name(db, ARTIFACT_NAMESPACE, name)
            if component is None:
                return {"error": "not_found", "detail": f"No artifact named '{name}'"}
            # The user's own artifacts only (or what their permissions allow).
            perm_own = f"sinas.components/{ARTIFACT_NAMESPACE}/{name}.update:own"
            perm_all = f"sinas.components/{ARTIFACT_NAMESPACE}/{name}.update:all"
            owns = str(component.user_id) == str(user_id)
            if not (check_permission(permissions, perm_all) or (owns and check_permission(permissions, perm_own))):
                raise PermissionError(f"The user may not change artifact '{name}'")
            from app.services.resources.patch import PatchRejected, patched_spec

            patch: dict[str, Any] = {}
            if "html" in arguments:
                patch["source_code"] = arguments["html"]
            if "title" in arguments:
                patch["title"] = arguments["title"]
            patch.update(_declared_resources(arguments))
            locked = await applier.find_by_id(ctx, component.id)
            try:
                spec = patched_spec(applier.spec_from_row(locked), patch)
            except PatchRejected as e:
                return {"error": "validation_error", "detail": str(e.detail)}
            await _check_within_agent(db, spec, agent)
            async with db.begin_nested():
                await applier.apply(spec, ctx, existing=locked)
            await db.commit()
            return _shown(locked, user_id, arguments.get("input"))

        return {"error": "unknown_tool", "detail": f"Unknown artifact tool: {tool_name}"}
    except PermissionError as e:
        return {"error": "permission_denied", "detail": str(e)}
    except ValidationError as e:
        return {"error": "validation_error", "detail": "; ".join(err["msg"] for err in e.errors())}
    except ApplierError as e:
        return {"error": "rejected", "detail": str(e)}
    except Exception as e:  # pragma: no cover - logged and reported to the model
        logger.error(f"Artifact tool {tool_name} failed: {e}", exc_info=True)
        return {"error": "internal_error", "detail": str(e)}
