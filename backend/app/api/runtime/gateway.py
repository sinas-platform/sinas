"""POST /mcp/{namespace}/{name}: a manifest's exposed tools as an MCP server.

Streamable HTTP, stateless, JSON responses: every request authenticates on
its own (Bearer API key or access token, or X-API-Key), so there is no
session for a load balancer to pin and nothing to replay. The MCP SDK's
server runs the protocol; the two handlers below answer `tools/list` and
`tools/call` for the manifest and caller of the current request, which
reach them through a context variable set before the SDK sees the request.

Mounted as a raw ASGI app (app.mount("/mcp", ...)) because the SDK's
transport is one, and because authentication must run before any body is
read by it.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import MCPError

from app.core.database import AsyncSessionLocal
from app.models.manifest import Manifest
from app.services import gateway

logger = logging.getLogger(__name__)


@dataclass
class _Caller:
    manifest_id: str
    manifest_ns: str
    manifest_name: str
    user_id: str
    email: str | None
    permissions: dict[str, bool]
    token: str


_caller: ContextVar[_Caller | None] = ContextVar("gateway_caller", default=None)


async def _load_manifest(db, caller: _Caller) -> Manifest:
    manifest = await Manifest.get_by_name(db, caller.manifest_ns, caller.manifest_name)
    if manifest is None or manifest.is_active is False:
        raise MCPError(types.INVALID_REQUEST, "Manifest is gone")
    return manifest


async def _on_list_tools(ctx, params) -> types.ListToolsResult:
    caller = _caller.get()
    if caller is None:  # pragma: no cover - the ASGI wrapper always sets it
        raise MCPError(types.INVALID_REQUEST, "Unauthenticated")
    async with AsyncSessionLocal() as db:
        manifest = await _load_manifest(db, caller)
        tools = await gateway.list_tools(db, manifest, caller.user_id, caller.permissions)
    return types.ListToolsResult(
        tools=[
            types.Tool(
                name=t.name,
                description=t.description,
                inputSchema=t.input_schema,
                outputSchema=t.output_schema,
                _meta={"sinas": {"type": t.type, "ref": t.ref}},
            )
            for t in tools
        ]
    )


async def _on_call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
    caller = _caller.get()
    if caller is None:  # pragma: no cover
        raise MCPError(types.INVALID_REQUEST, "Unauthenticated")
    async with AsyncSessionLocal() as db:
        manifest = await _load_manifest(db, caller)
        tools = await gateway.list_tools(db, manifest, caller.user_id, caller.permissions)
        tool = next((t for t in tools if t.name == params.name), None)
        if tool is None:
            # Unknown and not-allowed look the same: the catalog is per caller.
            raise MCPError(types.INVALID_PARAMS, f"Unknown tool: {params.name}")
        result = await gateway.call_tool(
            db, manifest, tool,
            user_id=caller.user_id, user_email=caller.email, user_token=caller.token,
            arguments=dict(params.arguments or {}),
        )
    is_error = isinstance(result, dict) and "error" in result
    text = json.dumps(result, default=str, ensure_ascii=False)
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        structuredContent=result if (tool.output_schema and not is_error) else None,
        isError=is_error,
    )


def _build_server() -> Server:
    server = Server("sinas-gateway")
    server.add_request_handler("tools/list", types.PaginatedRequestParams, _on_list_tools)
    server.add_request_handler("tools/call", types.CallToolRequestParams, _on_call_tool)
    return server


class _Transport:
    """The SDK's session manager, started lazily on the loop that serves the
    first request (and again on a new loop — the test suite runs one per
    test). Stateless: a fresh server session per request, no stored state."""

    def __init__(self) -> None:
        self._manager: StreamableHTTPSessionManager | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._started: asyncio.Event | None = None
        self._stop: asyncio.Event | None = None
        self._lock = asyncio.Lock()

    async def manager(self) -> StreamableHTTPSessionManager:
        loop = asyncio.get_running_loop()
        if self._manager is not None and self._loop is loop:
            return self._manager
        async with self._lock:
            if self._manager is not None and self._loop is loop:
                return self._manager
            manager = StreamableHTTPSessionManager(
                app=_build_server(),
                json_response=True,
                stateless=True,
                # Behind our own authentication, on whatever host the deployment
                # answers to: the SDK's Host allowlist does not apply.
                security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=False),
            )
            started, stop = asyncio.Event(), asyncio.Event()

            async def run() -> None:
                async with manager.run():
                    started.set()
                    await stop.wait()

            loop.create_task(run(), name="sinas-gateway-mcp")
            await started.wait()
            self._manager, self._loop, self._started, self._stop = manager, loop, started, stop
            return manager


_transport = _Transport()


async def _send_json(send, status: int, body: dict[str, Any], headers: dict[str, str] | None = None) -> None:
    raw = json.dumps(body).encode()
    hdrs = [(b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode())]
    for k, v in (headers or {}).items():
        hdrs.append((k.lower().encode(), v.encode()))
    await send({"type": "http.response.start", "status": status, "headers": hdrs})
    await send({"type": "http.response.body", "body": raw})


def _bearer_challenge(scope) -> dict[str, str]:
    # The resource metadata URL is where an OAuth-capable client would go
    # next (RFC 9728); served once the OAuth follow-up lands. The header is
    # the standard way to say "Bearer token required" meanwhile.
    return {"WWW-Authenticate": 'Bearer realm="sinas-gateway"'}


async def _authenticate(token: str):
    """(user_id, email, permissions) for a Bearer API key or access token,
    through the same verifier every runtime endpoint uses."""
    from app.core.auth import verify_jwt_or_api_key

    async with AsyncSessionLocal() as db:
        return await verify_jwt_or_api_key(
            credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials=token),
            x_api_key=None,
            db=db,
            request=None,
        )


class GatewayASGI:
    """`/mcp/{namespace}/{name}` -> the manifest's MCP server for this caller."""

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":  # pragma: no cover - lifespan is the root app's
            return
        # Starlette keeps the full path on a mounted app and grows root_path.
        full, root = scope.get("path", ""), scope.get("root_path", "")
        rel = full[len(root):] if root and full.startswith(root) else full
        parts = [p for p in rel.split("/") if p]
        if len(parts) != 2:
            await _send_json(send, 404, {"detail": "Use /mcp/{namespace}/{name}"})
            return
        namespace, name = parts
        if scope["method"] not in ("POST", "GET", "DELETE"):
            await _send_json(send, 405, {"detail": "Method not allowed"})
            return

        headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope.get("headers", [])}
        token = headers.get("x-api-key")
        if not token:
            auth = headers.get("authorization", "")
            if auth.lower().startswith("bearer "):
                token = auth[7:].strip()
        if not token:
            await _send_json(send, 401, {"detail": "Missing authorization header"}, _bearer_challenge(scope))
            return
        try:
            user_id, email, permissions = await _authenticate(token)
        except HTTPException as e:
            await _send_json(send, e.status_code, {"detail": e.detail}, _bearer_challenge(scope))
            return

        async with AsyncSessionLocal() as db:
            manifest = await Manifest.get_by_name(db, namespace, name)
            if manifest is None or manifest.is_active is False:
                await _send_json(send, 404, {"detail": f"Manifest '{namespace}/{name}' not found"})
                return
            if not manifest.can_user_access(str(user_id), permissions, "read"):
                await _send_json(send, 403, {"detail": "Not authorized to use this gateway"})
                return
            caller = _Caller(
                manifest_id=str(manifest.id), manifest_ns=namespace, manifest_name=name,
                user_id=str(user_id), email=email, permissions=permissions, token=token,
            )

        reset = _caller.set(caller)
        try:
            manager = await _transport.manager()
            await manager.handle_request(scope, receive, send)
        finally:
            _caller.reset(reset)


gateway_app = GatewayASGI()
