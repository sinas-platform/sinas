"""State store model for agent/function/workflow state management."""
import uuid as uuid_lib
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, created_at, updated_at, uuid_pk


class State(Base):
    """Key-value state entry belonging to a Store."""

    __tablename__ = "states"

    id: Mapped[uuid_pk]
    user_id: Mapped[uuid_lib.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Store reference (replaces raw namespace)
    store_id: Mapped[uuid_lib.UUID] = mapped_column(
        ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Core key-value structure
    key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    # Encryption
    encrypted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    encrypted_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Sharing control
    visibility: Mapped[str] = mapped_column(
        String(20), nullable=False, default="private", index=True
    )
    # Options: "private" (owner only), "shared" (accessible by users with store permissions)

    # Metadata
    description: Mapped[Optional[str]] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    # Ranking and lifecycle
    relevance_score: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    expires_at: Mapped[Optional[datetime]]

    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="states")
    store: Mapped["Store"] = relationship("Store", back_populates="states")

    __table_args__ = (
        # Unique constraint: one key per user/store combination
        Index("uq_state_user_store_key", "user_id", "store_id", "key", unique=True),
        # Performance indexes
        Index("ix_states_store_visibility", "store_id", "visibility"),
        Index("ix_states_expires_at", "expires_at"),
    )
