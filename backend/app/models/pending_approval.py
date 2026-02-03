"""Pending tool call approval tracking."""
import uuid
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, created_at, uuid_pk


class PendingToolApproval(Base):
    """Track tool calls that require user approval before execution."""

    __tablename__ = "pending_tool_approvals"

    id: Mapped[uuid_pk]
    chat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chats.id"), nullable=False, index=True)
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    # Tool call details
    tool_call_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    function_namespace: Mapped[str] = mapped_column(String(255), nullable=False)
    function_name: Mapped[str] = mapped_column(String(255), nullable=False)
    arguments: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    # Context for resuming execution
    all_tool_calls: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False
    )  # All tool calls from assistant message
    conversation_context: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False
    )  # Provider, model, temp, etc.

    # Status
    approved: Mapped[bool] = mapped_column(
        Boolean, nullable=True
    )  # None=pending, True=approved, False=rejected
    created_at: Mapped[created_at]
