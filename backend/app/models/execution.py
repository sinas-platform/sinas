import enum
import uuid as uuid_lib
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, uuid_pk


class TriggerType(str, enum.Enum):
    WEBHOOK = "WEBHOOK"
    SCHEDULE = "SCHEDULE"
    AGENT = "AGENT"  # Triggered by agent/chat tool call
    MANUAL = "MANUAL"  # Triggered manually from UI
    API = "API"  # Triggered via runtime API
    CDC = "CDC"  # Triggered by CDC (Change Data Capture) polling
    HOOK = "HOOK"  # Triggered by message lifecycle hook
    PIPELINE = "PIPELINE"  # Child execution of a pipeline run (trigger_id = run_id)


class ExecutionStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    AWAITING_INPUT = "AWAITING_INPUT"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class Execution(Base):
    __tablename__ = "executions"

    id: Mapped[uuid_pk]
    user_id: Mapped[uuid_lib.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    execution_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    function_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    trigger_type: Mapped[TriggerType] = mapped_column(Enum(TriggerType), nullable=False)
    trigger_id: Mapped[uuid_lib.UUID] = mapped_column(String(255), nullable=False)
    chat_id: Mapped[Optional[uuid_lib.UUID]] = mapped_column(ForeignKey("chats.id"), index=True)
    status: Mapped[ExecutionStatus] = mapped_column(
        Enum(ExecutionStatus), default=ExecutionStatus.PENDING, nullable=False
    )
    input_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    output_data: Mapped[Optional[Any]] = mapped_column(JSON)
    error: Mapped[Optional[str]] = mapped_column(Text)
    traceback: Mapped[Optional[str]] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)

    # Stateful execution fields
    input_prompt: Mapped[Optional[str]] = mapped_column(Text)
    input_schema: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    container_id: Mapped[Optional[str]] = mapped_column(String(255))

    # Link to the tool_call that triggered this execution (for execution tree)
    tool_call_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)

    # Async-callback delivery status (null = no callback configured).
    # See runtime API /functions/.../execute/async with `callback_url`.
    callback_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    callback_response_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Link to parent batch (null = not part of a batch submission).
    batch_id: Mapped[Optional[uuid_lib.UUID]] = mapped_column(
        ForeignKey("batches.id"), nullable=True, index=True
    )

    # Relationships
    user: Mapped["User"] = relationship("User")
