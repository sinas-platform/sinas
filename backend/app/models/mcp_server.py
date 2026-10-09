"""MCP server model — a remote Model Context Protocol server whose tools agents can call.

One row per server endpoint (Streamable HTTP or SSE). Credentials never live
here: `auth.secret` names a Secret, resolved and decrypted at call time, the
same way connectors do it. The tool list itself is not stored — it is fetched
from the server at discovery time (and cached briefly in-process).
"""
import uuid
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, created_at, updated_at, uuid_pk
from .mixins import PermissionMixin


class McpServer(Base, PermissionMixin):
    """A remote MCP server exposed to agents as a tool source."""

    __tablename__ = "mcp_servers"

    id: Mapped[uuid_pk]
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    namespace: Mapped[str] = mapped_column(String(100), nullable=False, index=True, default="default")
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    url: Mapped[str] = mapped_column(Text, nullable=False)
    # "streamable_http" (default) or "sse" (the legacy HTTP+SSE transport).
    transport: Mapped[str] = mapped_column(
        String(32), nullable=False, default="streamable_http", server_default="streamable_http"
    )

    # Auth: {"type": "none|bearer|header", "secret": "SECRET_NAME", "header": "X-Api-Key"}
    auth: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict, server_default='{"type": "none"}')
    # Static headers sent on every request: {"X-Tenant": "acme"}
    headers: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")

    # Server-level tool filter (glob patterns on the server's tool names).
    # Empty allow list = every tool; deny wins over allow.
    tool_allow: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list, server_default="[]")
    tool_deny: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list, server_default="[]")

    # Per tool call; connect + tools/list use connect_timeout_seconds.
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60, server_default="60")
    connect_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=10, server_default="10")

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    # Config management
    managed_by: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    config_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    config_checksum: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]

    __table_args__ = (
        UniqueConstraint("namespace", "name", name="uq_mcp_server_namespace_name"),
    )

    @classmethod
    async def get_by_name(cls, db, namespace: str, name: str) -> Optional["McpServer"]:
        from sqlalchemy.future import select

        result = await db.execute(select(cls).where(cls.namespace == namespace, cls.name == name))
        return result.scalar_one_or_none()
