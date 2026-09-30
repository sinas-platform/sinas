"""Batch of executions — submitted by an external app, polled as a unit."""
import enum
import uuid as uuid_lib
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import GUID, Base, uuid_pk


class BatchKind(str, enum.Enum):
    FUNCTION = "FUNCTION"
    AGENT = "AGENT"


class BatchStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"   # all children completed successfully
    PARTIAL = "PARTIAL"       # all children terminal, ≥1 failed
    FAILED = "FAILED"         # all children failed
    CANCELLED = "CANCELLED"   # cancelled before all children terminated


class Batch(Base):
    """A submission of N executions targeting the same function or agent."""

    __tablename__ = "batches"

    id: Mapped[uuid_pk]
    user_id: Mapped[uuid_lib.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    kind: Mapped[BatchKind] = mapped_column(Enum(BatchKind), nullable=False)
    target_namespace: Mapped[str] = mapped_column(String(255), nullable=False)
    target_name: Mapped[str] = mapped_column(String(255), nullable=False)
    total: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[BatchStatus] = mapped_column(
        Enum(BatchStatus), default=BatchStatus.QUEUED, nullable=False, index=True
    )
    trigger_id_prefix: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    callback_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    batch_callback_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Aggregate-callback delivery status (null = no batch_callback_url set).
    callback_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    callback_response_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Provider-native batch mode. "queue" = children run through the internal
    # queue (default); "provider" = the whole batch was submitted to the LLM
    # provider's batch API (agent batches without tools only) and a scheduler
    # job polls it to completion.
    execution_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default="queue", server_default="queue"
    )
    provider_batch_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    llm_provider_id: Mapped[Optional[uuid_lib.UUID]] = mapped_column(GUID(), nullable=True)

    # Relationships
    user: Mapped["User"] = relationship("User")
