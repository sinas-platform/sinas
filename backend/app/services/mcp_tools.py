"""MCP tool converter — exposes a remote MCP server's tools as agent tools.

Mirrors ConnectorToolConverter: discovery builds OpenAI-format tool
definitions (with `_metadata.tool_type = "mcp"`), execution dispatches on
that metadata. Filtering happens twice: the server's own allow/deny lists
(what the resource exposes at all) and the agent binding's `tools` globs
(what this agent gets).
"""
from __future__ import annotations

import fnmatch
import logging
import re
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mcp_server import McpServer
from app.services import mcp_client
from app.services.mcp_client import MCP_TOOL_PREFIX, ListedTool, McpClientError

logger = logging.getLogger(__name__)


def server_allows(server: McpServer, tool_name: str) -> bool:
    """The resource-level filter: deny wins, an empty allow list means all."""
    if any(fnmatch.fnmatchcase(tool_name, pattern) for pattern in (server.tool_deny or [])):
        return False
    allow = server.tool_allow or []
    return not allow or any(fnmatch.fnmatchcase(tool_name, pattern) for pattern in allow)


def binding_allows(patterns: list[str], tool_name: str) -> bool:
    """The agent-level filter: empty means every tool the server allows."""
    return not patterns or any(fnmatch.fnmatchcase(tool_name, pattern) for pattern in patterns)


def _sanitize(name: str) -> str:
    # OpenAI function names allow [A-Za-z0-9_-]; MCP tool names may not.
    return re.sub(r"[^A-Za-z0-9_-]", "_", name) or "tool"


def parse_mcp_tool_name(tool_name: str) -> Optional[tuple[str, str, str]]:
    """(namespace, server, sanitized tool) from an mcp_ tool name, or None.
    The real MCP tool name lives in `_metadata.mcp_tool`; this is only a
    fallback for callers without metadata."""
    if not tool_name.startswith(MCP_TOOL_PREFIX):
        return None
    parts = tool_name[len(MCP_TOOL_PREFIX):].split("__", 2)
    if len(parts) != 3:
        return None
    return parts[0], parts[1], parts[2]


def _normalize_entry(entry: Any) -> tuple[str, list[str]]:
    if isinstance(entry, str):
        return entry, []
    if isinstance(entry, dict):
        return str(entry.get("server", "")), list(entry.get("tools") or [])
    return str(getattr(entry, "server", "")), list(getattr(entry, "tools", None) or [])


class McpToolConverter:
    """Converts a server's MCP tools to OpenAI-format tools and executes them."""

    async def get_available_tools(
        self,
        db: AsyncSession,
        enabled_mcp_servers: Optional[list[Any]],
        user_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Tool definitions for every enabled server that answers. A server
        that is missing, inactive or unreachable contributes no tools and a
        log line — never a failed chat turn."""
        tools: list[dict[str, Any]] = []
        for entry in enabled_mcp_servers or []:
            ref, patterns = _normalize_entry(entry)
            parts = ref.split("/", 1)
            if len(parts) != 2:
                logger.warning(f"Invalid MCP server reference: {ref!r}")
                continue
            namespace, name = parts
            server = await McpServer.get_by_name(db, namespace, name)
            if not server or not server.is_active:
                logger.warning(f"MCP server '{ref}' not found or inactive")
                continue
            try:
                listed = await mcp_client.list_tools(db, server, user_id)
            except McpClientError as e:
                logger.warning(f"MCP tools unavailable: {e}")
                continue
            except Exception as e:  # pragma: no cover - belt and braces
                logger.warning(f"MCP tools unavailable for '{ref}': {e}", exc_info=True)
                continue
            seen: set[str] = set()
            for tool in listed:
                if not server_allows(server, tool.name) or not binding_allows(patterns, tool.name):
                    continue
                definition = self._to_tool(server, tool, seen)
                tools.append(definition)
        return tools

    def _to_tool(self, server: McpServer, tool: ListedTool, seen: set[str]) -> dict[str, Any]:
        base = f"{MCP_TOOL_PREFIX}{server.namespace}__{server.name}__{_sanitize(tool.name)}"
        tool_name, n = base, 1
        while tool_name in seen:  # two MCP names sanitizing to the same one
            n += 1
            tool_name = f"{base}_{n}"
        seen.add(tool_name)
        description = tool.description or tool.title or tool.name
        parameters = tool.input_schema or {"type": "object", "properties": {}}
        if parameters.get("type") != "object":
            parameters = {"type": "object", "properties": {}}
        metadata: dict[str, Any] = {
            "tool_type": "mcp",
            "server_namespace": server.namespace,
            "server_name": server.name,
            "mcp_tool": tool.name,
        }
        if tool.annotations:
            metadata["annotations"] = tool.annotations
        return {
            "type": "function",
            "function": {
                "name": tool_name,
                "description": f"[{server.namespace}/{server.name}] {description}",
                "parameters": parameters,
                "_metadata": metadata,
            },
        }

    async def execute_tool(
        self,
        db: AsyncSession,
        tool_name: str,
        arguments: dict[str, Any],
        user_id: Optional[str],
        metadata: Optional[dict[str, Any]] = None,
        chat: Any = None,
    ) -> dict[str, Any]:
        """Call the tool and map its result. Errors are results, not raises."""
        metadata = metadata or {}
        namespace = metadata.get("server_namespace")
        name = metadata.get("server_name")
        mcp_tool = metadata.get("mcp_tool")
        if not (namespace and name and mcp_tool):
            parsed = parse_mcp_tool_name(tool_name)
            if not parsed:
                return {"error": f"Invalid MCP tool name: {tool_name}"}
            namespace, name, mcp_tool = parsed

        server = await McpServer.get_by_name(db, namespace, name)
        if not server or not server.is_active:
            return {"error": f"MCP server '{namespace}/{name}' not found or inactive"}
        if not server_allows(server, mcp_tool):
            return {"error": f"Tool '{mcp_tool}' is not allowed on MCP server '{namespace}/{name}'"}

        store_blob = await self._blob_store(db, chat, user_id, tool_name) if chat is not None else None
        try:
            logger.info(f"🔌 Calling MCP tool: {namespace}/{name}/{mcp_tool}")
            result = await mcp_client.call_tool(db, server, mcp_tool, arguments or {}, user_id)
        except McpClientError as e:
            logger.warning(f"MCP call failed: {e}")
            return {"error": str(e)}
        except Exception as e:  # pragma: no cover
            logger.error(f"MCP call failed: {namespace}/{name}/{mcp_tool}: {e}", exc_info=True)
            return {"error": str(e)}
        return await mcp_client.map_call_result(result, tool_name=mcp_tool, store_blob=store_blob)

    async def _blob_store(self, db: AsyncSession, chat: Any, user_id: Optional[str], tool_name: str):
        """Binary blocks go to the chat's workbench when the agent has one
        (the model gets a path, not a base64 wall); otherwise they inline."""
        from app.services.workbench import chat_has_workbench_enabled

        try:
            if chat is None or str(chat.user_id) != str(user_id):
                return None
            if not await chat_has_workbench_enabled(db, chat):
                return None
        except Exception as e:  # pragma: no cover
            logger.warning(f"Workbench check failed for MCP blob store: {e}")
            return None

        async def store(filename: str, content: bytes, mime_type: str) -> Optional[str]:
            from app.services.file_storage import get_storage
            from app.services.workbench import _write_bytes, get_or_create_workbench

            workbench = await get_or_create_workbench(db, chat)
            path = f"tool_results/{filename}"
            written = await _write_bytes(
                db,
                get_storage(),
                workbench,
                filename=path,
                content=content,
                content_type=mime_type,
                user_id=str(chat.user_id),
                visibility="private",
                file_metadata={"origin": "tool", "tool_name": tool_name},
            )
            if "error" in written:
                logger.warning(f"MCP blob write failed: {written['error']}")
                return None
            await db.commit()
            return path

        return store
