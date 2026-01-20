"""State store model for agent/function/workflow state management."""
from sqlalchemy import String, Text, Boolean, ForeignKey, JSON, Float, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import Optional, List, Dict, Any
import uuid as uuid_lib
from datetime import datetime

from .base import Base, uuid_pk, created_at, updated_at


class State(Base):
    """Flexible key-value store for agent states, function states, workflow states, and preferences."""
    __tablename__ = "states"

    id: Mapped[uuid_pk]
    user_id: Mapped[uuid_lib.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    group_id: Mapped[Optional[uuid_lib.UUID]] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), index=True
    )

    # Core key-value structure
    namespace: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    value: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)

    # Sharing control
    visibility: Mapped[str] = mapped_column(
        String(20), nullable=False, default="private", index=True
    )
    # Options: "private" (user only), "group" (group members), "public" (future: org-wide)

    # Metadata
    description: Mapped[Optional[str]] = mapped_column(Text)
    tags: Mapped[List[str]] = mapped_column(JSON, default=list, nullable=False)

    # Ranking and lifecycle
    relevance_score: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    expires_at: Mapped[Optional[datetime]]

    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="states")
    group: Mapped[Optional["Group"]] = relationship("Group", back_populates="states")

    __table_args__ = (
        # Unique constraint: one key per user/namespace combination
        Index(
            "uq_state_user_namespace_key",
            "user_id", "namespace", "key",
            unique=True
        ),
        # Performance indexes
        Index("ix_states_namespace_visibility", "namespace", "visibility"),
        Index("ix_states_expires_at", "expires_at"),
    )
