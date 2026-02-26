import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, created_at, updated_at, uuid_pk


class Chat(Base):
    __tablename__ = "chats"

    id: Mapped[uuid_pk]
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    # Agent reference (FK can be null if agent deleted, namespace/name preserved for audit trail)
    agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("agents.id"), index=True)
    agent_namespace: Mapped[Optional[str]] = mapped_column(String(255))
    agent_name: Mapped[Optional[str]] = mapped_column(String(255))

    title: Mapped[str] = mapped_column(String(500), nullable=False)

    archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False, index=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    # Chat metadata for creation context (agent input, resolved function params, etc.)
    chat_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, default=None)
    # Example: {"agent_input": {"my_city": "London"}, "resolved_function_params": {"check_weather": {"city": "London"}}}

    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="chats")
    agent: Mapped[Optional["Agent"]] = relationship("Agent", back_populates="chats")
    messages: Mapped[list["Message"]] = relationship(
        "Message",
        back_populates="chat",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid_pk]
    chat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chats.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user, assistant, system, tool
    content: Mapped[Optional[str]] = mapped_column(Text)
    tool_calls: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSON)
    tool_call_id: Mapped[Optional[str]] = mapped_column(String(255))
    name: Mapped[Optional[str]] = mapped_column(String(255))  # For tool response messages
    created_at: Mapped[created_at]

    # Relationships
    chat: Mapped["Chat"] = relationship("Chat", back_populates="messages")
