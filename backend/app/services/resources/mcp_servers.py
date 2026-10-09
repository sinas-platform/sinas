"""MCP servers applier."""

from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select

from app.models.mcp_server import McpServer
from app.schemas.spec.mcp_server import McpServerAuthSpec, McpServerSpec
from app.services.resources.base import ApplyContext, ResourceApplier
from app.services.resources.history import redact


def _redacted_url(url: str | None) -> str | None:
    """A URL with its credentials redacted: userinfo and the query (a token
    often sits in either, e.g. https://<token>@host or ?key=...)."""
    if not url:
        return url
    parts = urlsplit(url)
    netloc = parts.netloc
    if "@" in netloc:
        userinfo, host = netloc.rsplit("@", 1)
        netloc = f"{redact(userinfo)}@{host}"
    query = redact(parts.query) if parts.query else ""
    return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))


class McpServerApplier(ResourceApplier[McpServerSpec]):
    kind = "mcp_servers"
    label = "MCP server"
    noun = "MCP server"
    config_section = "mcpServers"
    spec_model = McpServerSpec
    model = McpServer
    keep_unless_declared = ("is_active",)

    def key_of(self, spec: McpServerSpec) -> str:
        return spec.key

    def key_of_row(self, row: McpServer) -> str:
        return f"{row.namespace}/{row.name}"

    def config_key(self, item: Any) -> str:
        return f"{item.namespace}/{item.name}"

    async def find(self, ctx: ApplyContext, key: str) -> McpServer | None:
        namespace, name = key.split("/", 1)
        return (
            await ctx.db.execute(
                select(McpServer)
                .where(McpServer.namespace == namespace, McpServer.name == name)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    def spec_from_row(self, row: McpServer) -> McpServerSpec:
        auth = {k: v for k, v in (row.auth or {}).items() if v is not None}
        auth.setdefault("type", "none")
        return McpServerSpec.model_construct(
            namespace=row.namespace,
            name=row.name,
            description=row.description or None,
            url=row.url,
            transport=row.transport or "streamable_http",
            auth=McpServerAuthSpec.model_construct(**auth),
            headers=dict(row.headers or {}),
            tool_allow=list(row.tool_allow or []),
            tool_deny=list(row.tool_deny or []),
            timeout_seconds=row.timeout_seconds,
            connect_timeout_seconds=row.connect_timeout_seconds,
            is_active=row.is_active is not False,
        )

    def new_row(self, spec: McpServerSpec, ctx: ApplyContext) -> McpServer:
        return McpServer(user_id=uuid.UUID(str(ctx.owner_user_id)))

    def write_fields(self, row: McpServer, spec: McpServerSpec) -> None:
        row.namespace = spec.namespace
        row.name = spec.name
        row.description = spec.description
        row.url = spec.url
        row.transport = spec.transport
        # Stored snake_case, without nulls — what the runtime reads.
        row.auth = spec.auth.model_dump(mode="json", exclude_none=True)
        row.headers = dict(spec.headers)
        row.tool_allow = list(spec.tool_allow)
        row.tool_deny = list(spec.tool_deny)
        row.timeout_seconds = spec.timeout_seconds
        row.connect_timeout_seconds = spec.connect_timeout_seconds
        row.is_active = spec.is_active

    # ---- history: no secret values ------------------------------------------

    def history_spec(self, spec: McpServerSpec) -> dict[str, Any]:
        """Static headers are where API keys end up, and a URL can carry
        credentials; history shows them redacted (a changed value still shows
        as a change). `auth.secret` is a Secret's name, not its value."""
        state = spec.canonical()
        state["headers"] = {key: redact(value) for key, value in (spec.headers or {}).items()}
        state["url"] = _redacted_url(spec.url)
        return state

    def secret_values(self, spec: McpServerSpec) -> dict[str, Any]:
        secrets: dict[str, Any] = {}
        if spec.headers:
            secrets["headers"] = dict(spec.headers)
        if _redacted_url(spec.url) != spec.url:
            secrets["url"] = spec.url
        return secrets

    def with_secrets(self, state: dict[str, Any], secrets: dict[str, Any]) -> dict[str, Any]:
        state = dict(state)
        if "headers" in secrets:
            state["headers"] = secrets["headers"]
        if "url" in secrets:
            state["url"] = secrets["url"]
        return state
