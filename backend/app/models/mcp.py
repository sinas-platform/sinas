from sqlalchemy import String, Text, Boolean, DateTime, JSON, ForeignKey, select
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, Dict, Any
import uuid

from .base import Base, uuid_pk, created_at, updated_at


class MCPServer(Base):
    __tablename__ = "mcp_servers"

    id: Mapped[uuid_pk]
    group_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("groups.id"), index=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    protocol: Mapped[str] = mapped_column(String(20), nullable=False)  # websocket or http
    api_key: Mapped[Optional[str]] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_connected: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True))
    connection_status: Mapped[str] = mapped_column(
        String(50), default="disconnected", nullable=False
    )  # connected, disconnected, error
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]

    # Config tracking
    managed_by: Mapped[Optional[str]] = mapped_column(Text)
    config_name: Mapped[Optional[str]] = mapped_column(Text)
    config_checksum: Mapped[Optional[str]] = mapped_column(Text)

    # Relationships
    group: Mapped[Optional["Group"]] = relationship("Group")

    @classmethod
    async def get_by_name(cls, db: AsyncSession, name: str) -> Optional["MCPServer"]:
        """Get MCP server by name."""
        result = await db.execute(
            select(cls).where(cls.name == name, cls.is_active == True)
        )
        return result.scalar_one_or_none()


