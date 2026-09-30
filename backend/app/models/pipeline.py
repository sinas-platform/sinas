"""Pipeline models — linear typed step sequences fired by triggers.

See backend/docs/adrs/2026-07-28-pipelines-triggers-and-linear-steps.md.
"""
import uuid
from typing import Any, Optional
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, created_at, updated_at, uuid_pk
from .execution import TriggerType
from .mixins import PermissionMixin


class Pipeline(Base, PermissionMixin):
    """A named linear sequence of typed steps (connector/function/agent/query/load).

    Steps are stored exactly as validated (camelCase config keys, `.$` mapping
    keys intact) — one representation end-to-end, like input_schema JSON blobs.
    """

    __tablename__ = "pipelines"
    __table_args__ = (UniqueConstraint("namespace", "name", name="uq_pipeline_namespace_name"),)

    id: Mapped[uuid_pk]
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    namespace: Mapped[str] = mapped_column(String(255), nullable=False, default="default", index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text)

    # JSON Schema validating run input (trigger payload / tool args / manual run body)
    input_schema: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    # Ordered step list; see schemas/pipeline.py for the validated shape.
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)

    # Per-user fan-out: {"connector": "ns/name", "disableAfterFailures": N} or NULL (shared).
    per_user: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    # Agent-tool exposure
    as_tool: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    tool_description: Mapped[Optional[str]] = mapped_column(Text)

    # Budget for inline (tool / sync) runs, seconds.
    sync_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=120, server_default="120")

    # "single" | "parallel" | NULL (= single when a cursor step exists, else parallel)
    concurrency: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)

    # Shared-mode auto-deactivation after N consecutive failed runs (NULL = off).
    disable_after_failures: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Final-output shaping: {"output": <literal>} or {"output.$": "<jmespath>"} or NULL
    # (= last step's output). Stored raw and resolved by the mapping module.
    output_mapping: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    # Shared-mode cursor bookmark (perUser pipelines use pipeline_cursors instead).
    cursor_value: Mapped[Optional[str]] = mapped_column(Text)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    error_message: Mapped[Optional[str]] = mapped_column(Text)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    # Config tracking
    managed_by: Mapped[Optional[str]] = mapped_column(Text)
    config_name: Mapped[Optional[str]] = mapped_column(Text)
    config_checksum: Mapped[Optional[str]] = mapped_column(Text)

    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]

    user: Mapped["User"] = relationship("User")

    @classmethod
    async def get_by_name(cls, db: AsyncSession, namespace: str, name: str) -> Optional["Pipeline"]:
        result = await db.execute(select(cls).where(cls.namespace == namespace, cls.name == name))
        return result.scalar_one_or_none()

    def get_cursor_step(self) -> Optional[dict[str, Any]]:
        """The step declaring cursor config, or None (max one, validated)."""
        for step in (self.steps or []):
            if step.get("cursor"):
                return step
        return None

    def effective_concurrency(self) -> str:
        if self.concurrency in ("single", "parallel"):
            return self.concurrency
        return "single" if self.get_cursor_step() else "parallel"


class PipelineRun(Base):
    """One execution of a pipeline. Doubles as the dead-letter record: it stores
    the full input, per-step summaries, and the error, and can be replayed."""

    __tablename__ = "pipeline_runs"

    id: Mapped[uuid_pk]
    pipeline_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pipelines.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    # The user the run executed as (for perUser fan-out: the connected user).
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    trigger_type: Mapped[TriggerType] = mapped_column(Enum(TriggerType), nullable=False)
    trigger_id: Mapped[Optional[str]] = mapped_column(String(255))

    # running | succeeded | failed | timed_out
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running", index=True)

    input: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    # [{name, type, status, startedAt, durationMs, executionId?, chatId?, error?}]
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    # Final run output (output_mapping applied), persisted on success so the
    # record matches what the live caller was given.
    output: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text)

    cursor_before: Mapped[Optional[str]] = mapped_column(Text)
    cursor_after: Mapped[Optional[str]] = mapped_column(Text)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)

    pipeline: Mapped["Pipeline"] = relationship("Pipeline")


class PipelineCursor(Base):
    """Per-(pipeline, user) bookmark + failure state for perUser pipelines."""

    __tablename__ = "pipeline_cursors"
    __table_args__ = (
        UniqueConstraint("pipeline_id", "user_id", name="uq_pipeline_cursor_pipeline_user"),
    )

    id: Mapped[uuid_pk]
    pipeline_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pipelines.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cursor_value: Mapped[Optional[str]] = mapped_column(Text)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    updated_at: Mapped[updated_at]
