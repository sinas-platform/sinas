"""Tool call result storage for context window management."""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from .base import Base
from .base import GUID


class ToolCallResult(Base):
    """Persistent storage for tool call results.

    Enables:
    - Context window management: older results replaced with compact references
    - On-demand retrieval via built-in retrieve_tool_result tool
    - Direct execution tracking for UI-initiated tool calls

    Table is partitioned by created_at (monthly) for efficient retention cleanup.
    """

    __tablename__ = "tool_call_results"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    tool_call_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    chat_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID(), nullable=True, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, index=True)
    tool_name: Mapped[str] = mapped_column(String(500), nullable=False)
    arguments: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    result: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    result_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # "agent" | "direct"
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = {
        "postgresql_partition_by": "RANGE (created_at)",
    }

    @staticmethod
    def default_expires_at() -> datetime:
        days = getattr(settings, "tool_result_retention_days", 30)
        return datetime.now(timezone.utc) + timedelta(days=days)
