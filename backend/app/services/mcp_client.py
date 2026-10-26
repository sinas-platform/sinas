"""MCP client — connect to a configured McpServer, list and call its tools.

See docs/adrs/2026-10-09-mcp-client.md. Transport scope is remote HTTP only
(Streamable HTTP, plus the legacy HTTP+SSE transport); stdio servers would
mean arbitrary process execution inside the backend and are deliberately
not supported.

Credentials: `auth.secret` names a Secret (private overrides shared for the
calling user, as for connectors) and is decrypted here, in the backend, at
call time. The sandbox never sees it.

Connections are per operation: one for a tools/list, one per tools/call,
one per tasks/* request. That is also the 2026-07-28 protocol's own model:
it has no sessions, so state across calls lives in explicit handles.

Tasks (the `io.modelcontextprotocol/tasks` extension, 2026-07-28): every
connection declares support, so a server may answer `tools/call` with a task
handle instead of a result. `call_tool` polls it for a short while and
returns the result when it lands in time; otherwise it returns the task, and
`mcp_tasks` keeps it durably and polls it in the background.
The tool list is cached briefly in-process (settings.mcp_tool_list_ttl_seconds)
so a chat turn does not re-handshake with every enabled server; a failed
listing is cached too, for a shorter time, so a dead server degrades to "no
tools" without being hammered on every message.
"""
from __future__ import annotations

import asyncio
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
from typing import Any, Literal, Optional, Union
from urllib.parse import urlsplit, urlunsplit

import httpx2
from mcp import Client
from mcp.client.extension import ClaimContext, ClientExtension, ResultClaim
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.types import CallToolResult, TextContent
from mcp_types import Request, RequestParams, Result
from pydantic import ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.secret_refs import describe_secret_ref, secret_ref_name
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


# --------------------------------------------------------------- tasks (protocol)


TASKS_EXTENSION = "io.modelcontextprotocol/tasks"
TERMINAL_TASK_STATUSES = frozenset({"completed", "failed", "cancelled"})


class _TaskFields(Result):
    """The `Task` fields of the ext-tasks schema (2026-07-28). Unknown fields
    are kept: the extension may grow."""

    model_config = ConfigDict(extra="allow")

    task_id: str
    status: str
    status_message: Optional[str] = None
    created_at: Optional[str] = None
    last_updated_at: Optional[str] = None
    ttl_ms: Optional[int] = None
    poll_interval_ms: Optional[int] = None


class CreatedTask(_TaskFields):
    """`CreateTaskResult`: a `tools/call` answered with a task handle."""

    result_type: Literal["task"]


class TaskState(_TaskFields):
    """A `tasks/get` answer: the task, with `inputRequests` while it waits for
    input, `result` once completed, `error` once failed."""

    result_type: Optional[str] = None
    input_requests: Optional[dict[str, Any]] = None
    result: Optional[dict[str, Any]] = None
    error: Optional[dict[str, Any]] = None


class _Ack(Result):
    model_config = ConfigDict(extra="allow")
    result_type: Optional[str] = None


class _TaskParams(RequestParams):
    task_id: str


class _UpdateTaskParams(RequestParams):
    task_id: str
    input_responses: dict[str, Any]


# Each carries the task id in the Mcp-Name header too (the spec's routing
# header for tasks/*, so a load balancer can send it where the task lives).
class _GetTask(Request[_TaskParams, Literal["tasks/get"]]):
    method: Literal["tasks/get"] = "tasks/get"
    name_param = "taskId"


class _UpdateTask(Request[_UpdateTaskParams, Literal["tasks/update"]]):
    method: Literal["tasks/update"] = "tasks/update"
    name_param = "taskId"


class _CancelTask(Request[_TaskParams, Literal["tasks/cancel"]]):
    method: Literal["tasks/cancel"] = "tasks/cancel"
    name_param = "taskId"


class TaskPending(Exception):
    """Raised out of the claim resolver when a task is still running when the
    inline wait ends (or needs input); carries the task's latest state."""

    def __init__(self, task: TaskState):
        super().__init__(f"task {task.task_id} is {task.status}")
        self.task = task


def poll_interval_seconds(task: _TaskFields, *, floor: float, ceiling: float, default: float) -> float:
    """The server's suggested interval, kept within [floor, ceiling]."""
    if task.poll_interval_ms is None or task.poll_interval_ms < 0:
        return default
    return min(max(task.poll_interval_ms / 1000.0, floor), ceiling)


def task_outcome(task: TaskState) -> CallToolResult:
    """What the original `tools/call` would have returned, for a task that
    reached a terminal status."""
    if task.status == "completed":
        return CallToolResult.model_validate(task.result or {"content": []})
    if task.status == "failed":
        error = task.error or {}
        message = error.get("message") or task.status_message or "the task failed"
        return CallToolResult(content=[TextContent(type="text", text=f"Task failed: {message}")], is_error=True)
    message = task.status_message or "the task was cancelled"
    return CallToolResult(content=[TextContent(type="text", text=f"Task cancelled: {message}")], is_error=True)


class _TasksExtension(ClientExtension):
    """Declares Tasks support and resolves a task answer by polling it for up
    to `wait_seconds`. Still running after that, or waiting for input, the
    resolver raises TaskPending: the caller takes it from there."""

    identifier = TASKS_EXTENSION

    def __init__(self, wait_seconds: float):
        self._wait = max(0.0, wait_seconds)

    def claims(self):
        return (ResultClaim(result_type="task", model=CreatedTask, resolve=self._resolve),)

    async def _resolve(self, created: CreatedTask, ctx: ClaimContext) -> CallToolResult:
        task = TaskState.model_validate(created.model_dump(by_alias=True, exclude={"result_type"}))
        deadline = time.monotonic() + self._wait
        while True:
            if task.status in TERMINAL_TASK_STATUSES:
                return task_outcome(task)
            if task.status == "input_required":
                raise TaskPending(task)
            interval = poll_interval_seconds(task, floor=0.2, ceiling=5.0, default=1.0)
            if time.monotonic() + interval >= deadline:
                raise TaskPending(task)
            await asyncio.sleep(interval)
            try:
                # Each look is bounded by what is left of the wait, so a slow
                # answer can't hold the turn past it.
                task = await _send_get(ctx.session, task.task_id, timeout=max(0.1, deadline - time.monotonic()))
            except Exception as e:
                # The task exists on the server whatever this look did: hand
                # it on (with what we last knew) to be polled again, rather
                # than lose the handle.
                logger.info(f"MCP task {task.task_id}: inline poll failed ({e}); handing it on")
                raise TaskPending(task) from e


async def _send_get(session: Any, task_id: str, timeout: Optional[float] = None) -> TaskState:
    return await session.send_request(
        _GetTask(params=_TaskParams(task_id=task_id)), TaskState, request_read_timeout_seconds=timeout
    )


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
    secret_name = secret_ref_name(auth.get("secret"))
    if not secret_name:
        raise McpClientError(f"MCP server '{server.namespace}/{server.name}': auth.secret is not set")
    value = await connector_service._resolve_secret_value(db, secret_name, user_id)
    if value is None:
        raise McpClientError(
            f"MCP server '{server.namespace}/{server.name}': {describe_secret_ref(secret_name)} not found"
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
    task_wait_seconds: float = 0.0,
) -> AsyncIterator[Client]:
    """A connected, initialized SDK client for `server`. `headers` are the
    resolved request headers when the caller already has them.
    `task_wait_seconds`: how long a tool call answered with a task is polled
    before it is handed back as a TaskState."""
    if headers is None:
        headers = await resolve_headers(db, server, user_id)
    # Response caching is ours (tools/list below); the SDK's would add a
    # second, per-connection layer that never gets a hit.
    client = Client(
        open_transport(server, headers),
        read_timeout_seconds=read_timeout,
        cache=None,
        extensions=[_TasksExtension(task_wait_seconds)],
    )
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


# Entries past their TTL are dropped on every lookup, and the cache is
# capped (oldest expiry first) so per-user, per-credential keys cannot
# accumulate in a long-lived worker.
_TOOL_CACHE_MAX_ENTRIES = 512


def _prune_tool_cache(now: float) -> None:
    expired = [k for k, (until, _) in _tool_cache.items() if until <= now]
    for k in expired:
        _tool_cache.pop(k, None)
    if len(_tool_cache) > _TOOL_CACHE_MAX_ENTRIES:
        for k, _ in sorted(_tool_cache.items(), key=lambda kv: kv[1][0])[: len(_tool_cache) - _TOOL_CACHE_MAX_ENTRIES]:
            _tool_cache.pop(k, None)


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
        seen_cursors: set[str] = set()
        pages = 0
        deadline = time.monotonic() + settings.mcp_list_tools_total_timeout_seconds
        try:
            while True:
                # A server that keeps paging (repeating a cursor, or never
                # ending) must not hold a chat turn or grow memory forever.
                pages += 1
                if pages > settings.mcp_list_tools_max_pages:
                    raise McpClientError(
                        f"MCP server '{server.namespace}/{server.name}' returned more than "
                        f"{settings.mcp_list_tools_max_pages} pages of tools; listing abandoned"
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise McpClientError(
                        f"MCP server '{server.namespace}/{server.name}' took longer than "
                        f"{settings.mcp_list_tools_total_timeout_seconds}s to list its tools; listing abandoned"
                    )
                try:
                    # The total budget bounds the in-flight request too, not
                    # just the decision to send the next one.
                    page = await asyncio.wait_for(client.list_tools(cursor=cursor), timeout=remaining)
                except asyncio.TimeoutError:
                    raise McpClientError(
                        f"MCP server '{server.namespace}/{server.name}' took longer than "
                        f"{settings.mcp_list_tools_total_timeout_seconds}s to list its tools; listing abandoned"
                    )
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
                if cursor in seen_cursors:
                    raise McpClientError(
                        f"MCP server '{server.namespace}/{server.name}' repeated a tools page cursor; listing abandoned"
                    )
                seen_cursors.add(cursor)
        except McpClientError:
            raise
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
    _prune_tool_cache(now)
    if use_cache:
        cached = _tool_cache.get(key)
        if cached and cached[0] > now:
            if isinstance(cached[1], McpClientError):
                raise cached[1]
            return list(cached[1])
    try:
        tools = await _fetch_tools(db, server, user_id, headers)
    except McpClientError as e:
        # The lifetime starts when the fetch ENDS: a slow failure must not
        # be written already expired, or every message reconnects and waits.
        if settings.mcp_tool_list_failure_ttl_seconds > 0:
            _tool_cache[key] = (time.monotonic() + settings.mcp_tool_list_failure_ttl_seconds, e)
        raise
    if settings.mcp_tool_list_ttl_seconds > 0:
        _tool_cache[key] = (time.monotonic() + settings.mcp_tool_list_ttl_seconds, tools)
    return tools


# --------------------------------------------------------------- tools/call


async def call_tool(
    db: AsyncSession,
    server: McpServer,
    tool_name: str,
    arguments: dict[str, Any],
    user_id: Optional[str],
    *,
    task_wait_seconds: float = 0.0,
) -> Union[CallToolResult, TaskState]:
    """The tool's result; or, when the server answered with a task that is
    still running after `task_wait_seconds` (or waits for input), the task."""
    async with connect(
        db, server, user_id, read_timeout=float(server.timeout_seconds), task_wait_seconds=task_wait_seconds
    ) as client:
        try:
            return await client.call_tool(tool_name, arguments or {})
        except TaskPending as pending:
            return pending.task
        except Exception as e:
            raise McpClientError(_describe(server, e)) from e


async def get_task(db: AsyncSession, server: McpServer, user_id: Optional[str], task_id: str) -> TaskState:
    async with connect(db, server, user_id, read_timeout=float(server.connect_timeout_seconds)) as client:
        try:
            return await _send_get(client.session, task_id)
        except Exception as e:
            raise McpClientError(_describe(server, e)) from e


async def update_task(
    db: AsyncSession, server: McpServer, user_id: Optional[str], task_id: str, input_responses: dict[str, Any]
) -> None:
    async with connect(db, server, user_id, read_timeout=float(server.connect_timeout_seconds)) as client:
        try:
            await client.session.send_request(
                _UpdateTask(params=_UpdateTaskParams(task_id=task_id, input_responses=input_responses)), _Ack
            )
        except Exception as e:
            raise McpClientError(_describe(server, e)) from e


async def cancel_task(db: AsyncSession, server: McpServer, user_id: Optional[str], task_id: str) -> None:
    async with connect(db, server, user_id, read_timeout=float(server.connect_timeout_seconds)) as client:
        try:
            await client.session.send_request(_CancelTask(params=_TaskParams(task_id=task_id)), _Ack)
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
