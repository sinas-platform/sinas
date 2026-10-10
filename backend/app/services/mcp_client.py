"""MCP client — connect to a configured McpServer, list and call its tools.

See docs/adrs/2026-10-09-mcp-client.md. Transport scope is remote HTTP only
(Streamable HTTP, plus the legacy HTTP+SSE transport); stdio servers would
mean arbitrary process execution inside the backend and are deliberately
not supported.

Credentials: `auth.secret` names a Secret (private overrides shared for the
calling user, as for connectors) and is decrypted here, in the backend, at
call time. The sandbox never sees it.

Connections are per operation: one for a tools/list, one per tools/call.
The tool list is cached briefly in-process (settings.mcp_tool_list_ttl_seconds)
so a chat turn does not re-handshake with every enabled server; a failed
listing is cached too, for a shorter time, so a dead server degrades to "no
tools" without being hammered on every message.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import logging
import mimetypes
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

import httpx2
from mcp import Client
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.types import CallToolResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.mcp_server import McpServer

logger = logging.getLogger(__name__)

# Tool name prefix for every MCP-sourced tool: mcp_<namespace>__<server>__<tool>
MCP_TOOL_PREFIX = "mcp_"

# httpx2 read timeout stays at the MCP default (a server may hold a response
# stream open); per-request deadlines are enforced by the SDK session.
_HTTP_READ_TIMEOUT = 300.0

# Called with (filename, content, mime_type); returns where the blob was
# stored (a workbench path), or None to inline it instead.
BlobStore = Callable[[str, bytes, str], Awaitable[Optional[str]]]


class McpClientError(Exception):
    """A call that could not be made (auth unresolvable, unreachable server,
    transport failure). The message is safe to show to the model."""


@dataclass(frozen=True)
class ListedTool:
    name: str
    description: Optional[str]
    input_schema: dict[str, Any]
    annotations: Optional[dict[str, Any]] = None
    title: Optional[str] = None


# --------------------------------------------------------------- auth + transport


async def resolve_headers(db: AsyncSession, server: McpServer, user_id: Optional[str]) -> dict[str, str]:
    """Static headers plus the resolved credential. Raises McpClientError
    when the configured Secret is missing: a request that was meant to be
    authenticated must never go out unauthenticated."""
    from app.services.connector_service import connector_service

    headers = {str(k): str(v) for k, v in (server.headers or {}).items()}
    auth = server.auth or {}
    auth_type = auth.get("type", "none")
    if auth_type == "none":
        return headers
    secret_name = auth.get("secret")
    if not secret_name:
        raise McpClientError(f"MCP server '{server.namespace}/{server.name}': auth.secret is not set")
    value = await connector_service._resolve_secret_value(db, secret_name, user_id)
    if value is None:
        raise McpClientError(
            f"MCP server '{server.namespace}/{server.name}': secret '{secret_name}' not found"
        )
    if auth_type == "bearer":
        headers["Authorization"] = f"Bearer {value}"
    elif auth_type == "header":
        headers[auth.get("header") or "X-Api-Key"] = value
    else:
        raise McpClientError(f"MCP server '{server.namespace}/{server.name}': unknown auth type '{auth_type}'")
    return headers


@contextlib.asynccontextmanager
async def _streamable_http(url: str, headers: dict[str, str], connect_timeout: float) -> AsyncIterator[Any]:
    http = create_mcp_http_client(
        headers=headers,
        timeout=httpx2.Timeout(connect_timeout, read=_HTTP_READ_TIMEOUT, write=30.0, pool=30.0),
    )
    async with http:
        async with streamable_http_client(url, http_client=http) as streams:
            yield streams


def open_transport(server: McpServer, headers: dict[str, str]):
    """The SDK transport for a server: an async context manager yielding the
    (read, write) streams. Module-level so tests can point it at an
    in-process server."""
    if server.transport == "sse":
        return sse_client(
            server.url,
            headers=headers,
            timeout=float(server.connect_timeout_seconds),
            sse_read_timeout=_HTTP_READ_TIMEOUT,
        )
    return _streamable_http(server.url, headers, float(server.connect_timeout_seconds))


@contextlib.asynccontextmanager
async def connect(
    db: AsyncSession,
    server: McpServer,
    user_id: Optional[str],
    *,
    read_timeout: float,
    headers: Optional[dict[str, str]] = None,
) -> AsyncIterator[Client]:
    """A connected, initialized SDK client for `server`. `headers` are the
    resolved request headers when the caller already has them."""
    if headers is None:
        headers = await resolve_headers(db, server, user_id)
    # Response caching is ours (tools/list below); the SDK's would add a
    # second, per-connection layer that never gets a hit.
    client = Client(open_transport(server, headers), read_timeout_seconds=read_timeout, cache=None)
    try:
        async with client:
            yield client
    except McpClientError:
        raise
    except Exception as e:  # transport, handshake, protocol
        raise McpClientError(_describe(server, e)) from e


def safe_url(url: str) -> str:
    """The URL without anything that can carry a credential: no userinfo, no
    query, no fragment. What error messages and logs may show."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<url>"
    host = parts.netloc.rsplit("@", 1)[-1]
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def _scrub(text: str, url: str) -> str:
    """Replace the raw URL, and each credential-bearing part of it, wherever
    the underlying error string repeats them."""
    if not url:
        return text
    text = text.replace(url, safe_url(url))
    try:
        parts = urlsplit(url)
    except ValueError:
        return text
    if "@" in parts.netloc:
        userinfo = parts.netloc.rsplit("@", 1)[0]
        text = text.replace(userinfo + "@", "***@")
        for piece in userinfo.split(":", 1):
            if len(piece) >= 4:
                text = text.replace(piece, "***")
    if parts.query:
        text = text.replace("?" + parts.query, "?***")
        for pair in parts.query.split("&"):
            value = pair.split("=", 1)[-1]
            if len(value) >= 4:
                text = text.replace(value, "***")
    return text


def _describe(server: McpServer, error: BaseException) -> str:
    # anyio task groups wrap failures in ExceptionGroups; the model wants the
    # innermost cause, not the group's "unhandled errors" wrapper.
    while isinstance(error, BaseExceptionGroup) and error.exceptions:
        error = error.exceptions[0]
    detail = _scrub(str(error) or type(error).__name__, server.url)
    return f"MCP server '{server.namespace}/{server.name}' ({safe_url(server.url)}): {detail}"


# --------------------------------------------------------------- tools/list (cached)


_tool_cache: dict[str, tuple[float, list[ListedTool] | McpClientError]] = {}


def _cache_key(server: McpServer, user_id: Optional[str], headers: dict[str, str]) -> str:
    """Per server version, per caller, per credential. updated_at changes on
    every edit (a changed URL or auth invalidates); the caller and a digest of
    the resolved headers keep one user's private credential — and the listing
    it yields — from being served to another, and a rotated secret from
    hitting a stale entry."""
    version = server.updated_at.isoformat() if server.updated_at else ""
    credential = hashlib.sha256(
        json.dumps(headers, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]
    return f"{server.id}:{version}:{user_id or ''}:{credential}"


def clear_tool_cache() -> None:
    _tool_cache.clear()


async def _fetch_tools(
    db: AsyncSession, server: McpServer, user_id: Optional[str], headers: dict[str, str]
) -> list[ListedTool]:
    tools: list[ListedTool] = []
    async with connect(
        db, server, user_id, read_timeout=float(server.connect_timeout_seconds), headers=headers
    ) as client:
        cursor: Optional[str] = None
        try:
            while True:
                page = await client.list_tools(cursor=cursor)
                for tool in page.tools:
                    annotations = tool.annotations.model_dump(exclude_none=True) if tool.annotations else None
                    tools.append(ListedTool(
                        name=tool.name,
                        description=tool.description,
                        input_schema=dict(tool.input_schema or {"type": "object", "properties": {}}),
                        annotations=annotations or None,
                        title=tool.title,
                    ))
                cursor = page.next_cursor
                if not cursor:
                    break
        except Exception as e:
            raise McpClientError(_describe(server, e)) from e
    return tools


async def list_tools(
    db: AsyncSession, server: McpServer, user_id: Optional[str], *, use_cache: bool = True
) -> list[ListedTool]:
    """The server's tools, before any allow/deny filtering. Raises
    McpClientError when the server can't be reached; callers that build a
    chat's tool list catch that and degrade to no tools from this server."""
    # Resolved first: a missing Secret refuses here, before any cache lookup,
    # so it is neither served from nor recorded in another user's entry.
    headers = await resolve_headers(db, server, user_id)
    key = _cache_key(server, user_id, headers)
    now = time.monotonic()
    if use_cache:
        cached = _tool_cache.get(key)
        if cached and cached[0] > now:
            if isinstance(cached[1], McpClientError):
                raise cached[1]
            return list(cached[1])
    try:
        tools = await _fetch_tools(db, server, user_id, headers)
    except McpClientError as e:
        if settings.mcp_tool_list_failure_ttl_seconds > 0:
            _tool_cache[key] = (now + settings.mcp_tool_list_failure_ttl_seconds, e)
        raise
    if settings.mcp_tool_list_ttl_seconds > 0:
        _tool_cache[key] = (now + settings.mcp_tool_list_ttl_seconds, tools)
    return tools


# --------------------------------------------------------------- tools/call


async def call_tool(
    db: AsyncSession,
    server: McpServer,
    tool_name: str,
    arguments: dict[str, Any],
    user_id: Optional[str],
) -> CallToolResult:
    async with connect(db, server, user_id, read_timeout=float(server.timeout_seconds)) as client:
        try:
            return await client.call_tool(tool_name, arguments or {})
        except Exception as e:
            raise McpClientError(_describe(server, e)) from e


# --------------------------------------------------------------- result mapping


# Common types the stdlib map resolves to a surprising (or no) extension.
_EXTENSIONS = {"audio/wav": "wav", "audio/x-wav": "wav", "audio/mpeg": "mp3", "image/jpeg": "jpg"}


def _extension(mime_type: Optional[str], fallback: str) -> str:
    mime = (mime_type or "").split(";")[0].strip().lower()
    if mime in _EXTENSIONS:
        return _EXTENSIONS[mime]
    ext = mimetypes.guess_extension(mime) if mime else None
    return (ext or fallback).lstrip(".")


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", name)[:80]


def blob_prefix_for(tool_name: str, tool_call_id: Optional[str]) -> str:
    """A per-call file stem: the (model-facing) tool name carries the server,
    the call id makes two calls to the same tool distinct — the workbench
    advances a file's version on rewrite, so a shared path would make an
    earlier result's pointer serve a later blob. A long or unusual call id is
    suffixed with a hash of the whole id, never truncated into ambiguity."""
    stem = _safe(tool_name)
    if not tool_call_id:
        return stem
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", tool_call_id)
    if len(safe_id) > 48 or safe_id != tool_call_id:
        safe_id = f"{safe_id[:48]}_{hashlib.sha256(tool_call_id.encode()).hexdigest()[:12]}"
    return f"{stem}_{safe_id}"


async def map_call_result(
    result: CallToolResult,
    *,
    tool_name: str,
    store_blob: Optional[BlobStore] = None,
    blob_prefix: Optional[str] = None,
) -> dict[str, Any]:
    """An MCP CallToolResult as a tool result dict for the model.

    - text blocks are joined into `text`;
    - `structuredContent` is passed through (and a text block that merely
      repeats it as JSON is dropped);
    - image/audio blocks and binary resources are stored through
      `store_blob` when one is given (the chat's workbench), otherwise
      inlined as data URLs in the universal content shape;
    - embedded text resources and resource links become `content` entries
      the model can read or follow;
    - `isError` becomes an `error` result (never a raised exception: a tool
      failing is a result the model should see and recover from).
    """
    texts: list[str] = []
    chunks: list[dict[str, Any]] = []
    stem = blob_prefix or f"mcp_{_safe(tool_name)}"

    async def blob(kind: str, data_b64: str, mime_type: Optional[str], index: int, *, uri: Optional[str] = None) -> None:
        mime = mime_type or "application/octet-stream"
        chunk: dict[str, Any] = {"type": kind, "mime_type": mime}
        if uri:
            chunk["uri"] = uri
        if store_blob is not None:
            try:
                raw = base64.b64decode(data_b64)
                filename = f"{stem}_{index}.{_extension(mime, 'bin')}"
                path = await store_blob(filename, raw, mime)
            except Exception as e:  # storage must not fail the call
                logger.warning(f"MCP blob store failed for {tool_name}: {e}")
                path = None
            if path:
                chunk["workbench_file"] = path
                chunk["size"] = len(raw)
                chunks.append(chunk)
                return
        if kind == "audio":
            chunk["data"] = data_b64
            chunk["format"] = _extension(mime, "wav")
        elif kind == "image":
            chunk["image"] = f"data:{mime};base64,{data_b64}"
        else:
            chunk["data"] = data_b64
        chunks.append(chunk)

    for index, block in enumerate(result.content or []):
        kind = getattr(block, "type", None)
        if kind == "text":
            texts.append(block.text)
        elif kind == "image":
            await blob("image", block.data, block.mime_type, index)
        elif kind == "audio":
            await blob("audio", block.data, block.mime_type, index)
        elif kind == "resource":
            resource = block.resource
            uri = str(getattr(resource, "uri", "") or "")
            mime = getattr(resource, "mime_type", None)
            if getattr(resource, "text", None) is not None:
                chunks.append({"type": "resource", "uri": uri, "mime_type": mime, "text": resource.text})
            elif getattr(resource, "blob", None) is not None:
                await blob("file", resource.blob, mime, index, uri=uri)
        elif kind == "resource_link":
            chunk = {"type": "resource_link", "uri": str(block.uri), "name": block.name}
            for field in ("title", "description", "mime_type", "size"):
                value = getattr(block, field, None)
                if value is not None:
                    chunk[field] = value
            chunks.append(chunk)
        else:  # a content kind this client doesn't know: keep it visible
            try:
                chunks.append(block.model_dump(mode="json", exclude_none=True))
            except Exception:
                chunks.append({"type": str(kind)})

    text = "\n".join(texts)
    if result.is_error:
        out: dict[str, Any] = {"error": text or f"MCP tool '{tool_name}' returned an error"}
        if chunks:
            out["content"] = chunks
        return out

    out = {}
    structured = result.structured_content
    if structured is not None:
        if len(texts) == 1 and _loads(texts[0]) == structured:
            text = ""  # the text block merely repeats the structured result
        elif (
            isinstance(structured, dict)
            and set(structured) == {"result"}
            and len(texts) == 1
            and texts[0] in (str(structured["result"]), json.dumps(structured["result"]))
        ):
            structured = None  # the SDK's auto-wrap of a scalar return: the text is enough
        if structured is not None:
            out["structured_content"] = structured
    if text or not (chunks or out):
        out["text"] = text
    if chunks:
        out["content"] = chunks
    return out


def _loads(value: str) -> Any:
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return object()  # never equal
