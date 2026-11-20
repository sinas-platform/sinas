"""MCP gateway: a manifest's exposed tools, served to external agents.

A manifest declares `exposed_tools`: functions, queries and pipelines that
agents and apps outside Sinas may call through `POST /mcp/{namespace}/{name}`
(app/api/runtime/gateway.py). This module turns that declaration into the
tool list one caller sees and runs a call, through the same converters the
chat loop uses — so permissions, locked parameters, the user context in
queries and the result store behave exactly as for an agent's tool call.

Access: a caller sees and runs an exposed tool only when their own
permissions allow executing the underlying resource. The manifest widens
nothing; it selects what is on offer.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.function import Function
from app.models.manifest import Manifest
from app.models.pipeline import Pipeline
from app.models.query import Query

logger = logging.getLogger(__name__)

_UNSAFE = re.compile(r"[^a-zA-Z0-9_-]")

# type -> (model, the chat converters' prefix for the type)
_MODELS = {"function": Function, "query": Query, "pipeline": Pipeline}


def default_tool_name(namespace: str, name: str) -> str:
    """`namespace__name`, in the character set every MCP client accepts."""
    return _UNSAFE.sub("_", f"{namespace}__{name}")[:64]


@dataclass(frozen=True)
class GatewayTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None
    type: str
    namespace: str
    resource: str

    @property
    def ref(self) -> str:
        return f"{self.namespace}/{self.resource}"


def _object_schema(schema: Any) -> dict[str, Any]:
    if isinstance(schema, dict) and schema.get("type") == "object":
        return schema
    return {"type": "object", "properties": {}}


async def list_tools(
    db: AsyncSession, manifest: Manifest, user_id: str, permissions: dict[str, bool]
) -> list[GatewayTool]:
    """The exposed tools `user_id` may call: the resource exists, is active,
    and the caller's permissions allow executing it. Entries the caller may
    not use are left out silently — the catalog itself is per caller."""
    tools: list[GatewayTool] = []
    taken: set[str] = set()
    for entry in manifest.exposed_tools or []:
        model = _MODELS.get(entry.get("type", ""))
        if model is None:
            continue
        namespace, name = entry.get("namespace", "default"), entry.get("name", "")
        row = await model.get_by_name(db, namespace=namespace, name=name)
        if row is None or getattr(row, "is_active", True) is False:
            logger.info("Gateway %s/%s: exposed %s %s/%s is missing or inactive",
                        manifest.namespace, manifest.name, entry.get("type"), namespace, name)
            continue
        if not row.can_user_access(str(user_id), permissions, "execute"):
            continue
        tool_name = entry.get("tool_name") or default_tool_name(namespace, name)
        if tool_name in taken:
            # Same default name from two types (function a/b and query a/b):
            # the later one carries its type. Explicit names are unique by
            # validation.
            tool_name = _UNSAFE.sub("_", f"{entry['type']}_{tool_name}")[:64]
        taken.add(tool_name)
        description = entry.get("description") or getattr(row, "description", None) or (
            f"{entry['type'].capitalize()} {namespace}/{name}"
        )
        output = getattr(row, "output_schema", None)
        tools.append(
            GatewayTool(
                name=tool_name,
                description=description,
                input_schema=_object_schema(getattr(row, "input_schema", None)),
                output_schema=output if isinstance(output, dict) and output.get("type") == "object" else None,
                type=entry["type"],
                namespace=namespace,
                resource=name,
            )
        )
    return tools


class RateLimitedError(Exception):
    def __init__(self, max_calls: int, window_seconds: int, retry_after: int):
        super().__init__(
            f"Rate limit exceeded: {max_calls} calls per {window_seconds}s for this caller; "
            f"retry after {retry_after}s"
        )
        self.max_calls, self.window_seconds, self.retry_after = max_calls, window_seconds, retry_after


def effective_limits(manifest: Manifest) -> tuple[int, int]:
    """(max_calls, window_seconds) for the manifest: its own, else the
    deployment default. max_calls 0 means no limit."""
    from app.core.config import settings

    limits = manifest.gateway_limits or {}
    max_calls = limits.get("max_calls", settings.gateway_rate_limit_max_calls)
    window = limits.get("window_seconds", settings.gateway_rate_limit_window_seconds)
    return int(max_calls), int(window)


async def enforce_rate_limit(manifest: Manifest, caller_key: str) -> None:
    """One counter per manifest and caller (an API key, else the user), a
    fixed window in Redis. Raises RateLimitedError past the cap. Redis being
    unreachable fails the call rather than opening the gate."""
    from fastapi import HTTPException

    from app.core.rate_limit import check_rate_limit

    max_calls, window = effective_limits(manifest)
    if max_calls <= 0:
        return
    try:
        await check_rate_limit(f"gateway:{manifest.id}:{caller_key}", max_calls, window)
    except HTTPException as e:
        retry_after = int((e.headers or {}).get("Retry-After", window))
        raise RateLimitedError(max_calls, window, retry_after) from None


async def call_tool(
    db: AsyncSession,
    manifest: Manifest,
    tool: GatewayTool,
    *,
    user_id: str,
    user_email: str | None,
    user_token: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Run one exposed tool as `user_id`, through the chat loop's converters,
    and record the call in the tool result store under source=gateway."""
    from app.services.function_tools import FunctionToolConverter
    from app.services.pipeline_tools import PIPELINE_TOOL_PREFIX, PipelineToolConverter
    from app.services.query_tools import QueryToolConverter

    started = time.monotonic()
    ref = tool.ref
    try:
        if tool.type == "function":
            result = await FunctionToolConverter().execute_function_tool(
                db, ref, arguments, user_id, user_token,
                enabled_functions=[ref], tool_call_id=None,
            )
        elif tool.type == "query":
            result = await QueryToolConverter().execute_query_tool(
                db, f"query_{tool.namespace}__{tool.resource}", arguments, user_id,
                user_email=user_email, enabled_queries=[ref],
            )
        elif tool.type == "pipeline":
            result = await PipelineToolConverter().execute_pipeline_tool(
                db, f"{PIPELINE_TOOL_PREFIX}{tool.namespace}__{tool.resource}", arguments, user_id,
                user_token, enabled_pipelines=[ref],
            )
        else:  # pragma: no cover - list_tools never yields another type
            result = {"error": f"Unsupported tool type {tool.type!r}"}
    except Exception as e:  # the converters raise on "not found"-class failures
        logger.warning("Gateway %s/%s: %s %s failed: %s", manifest.namespace, manifest.name, tool.type, ref, e)
        result = {"error": str(e)}
    if not isinstance(result, dict):
        result = {"result": result}

    await record_call(
        db, manifest, tool, user_id=user_id, arguments=arguments, result=result,
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    return result


def _clip_result(result: dict[str, Any]) -> dict[str, Any]:
    """The stored copy of a result: redacted like a request body and clipped
    to gateway_audit_result_max_chars, with a note of what was left out."""
    from app.core.config import settings
    from app.middleware.request_logger import redact_body

    safe = redact_body(result)
    text = json.dumps(safe, default=str)
    limit = settings.gateway_audit_result_max_chars
    if len(text) <= limit:
        return safe
    return {"_truncated": True, "_original_size": len(text), "_preview": text[:limit]}


async def record_call(
    db: AsyncSession, manifest: Manifest, tool: GatewayTool, *,
    user_id: str, arguments: dict[str, Any], result: dict[str, Any], duration_ms: int,
) -> None:
    """One audit row per call. Best effort: a failed write is logged, never
    turned into a failed call (the call already ran)."""
    from app.core.auth import current_api_key_id
    from app.middleware.request_logger import redact_body
    from app.models.gateway_call import GatewayCall

    key_id = current_api_key_id()
    # Read off the row before anything can expire it: a failed commit
    # expires loaded attributes, and the handler below must not touch them.
    manifest_id, namespace, name = manifest.id, manifest.namespace, manifest.name
    try:
        db.add(GatewayCall(
            manifest_id=manifest_id,
            manifest_namespace=namespace,
            manifest_name=name,
            user_id=uuid.UUID(str(user_id)),
            api_key_id=uuid.UUID(str(key_id)) if key_id else None,
            tool_name=tool.name,
            resource_type=tool.type,
            resource_ref=tool.ref,
            arguments=redact_body(arguments or {}),
            result=_clip_result(result if isinstance(result, dict) else {"result": result}),
            is_error=isinstance(result, dict) and "error" in result,
            duration_ms=duration_ms,
        ))
        await db.commit()
    except Exception as e:  # storage trouble: the call already ran, its result stands
        logger.warning("Gateway call on %s/%s not recorded: %s", namespace, name, e)
        try:
            await db.rollback()
        except Exception as rollback_error:  # pragma: no cover - the session is already broken
            logger.warning("Gateway audit rollback failed: %s", rollback_error)
