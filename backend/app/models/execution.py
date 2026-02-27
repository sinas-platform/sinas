import enum
import uuid as uuid_lib
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, uuid_pk


class TriggerType(str, enum.Enum):
    WEBHOOK = "WEBHOOK"
    SCHEDULE = "SCHEDULE"
    AGENT = "AGENT"  # Triggered by agent/chat tool call
    MANUAL = "MANUAL"  # Triggered manually from UI
    API = "API"  # Triggered via runtime API


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
    generator_state: Mapped[Optional[bytes]] = mapped_column(LargeBinary)
    input_prompt: Mapped[Optional[str]] = mapped_column(Text)
    input_schema: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)

    # Relationships
    user: Mapped["User"] = relationship("User")
    steps: Mapped[list["StepExecution"]] = relationship(
        "StepExecution", back_populates="execution", cascade="all, delete-orphan"
    )


class StepExecution(Base):
    __tablename__ = "step_executions"

    id: Mapped[uuid_pk]
    execution_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("executions.execution_id"), nullable=False, index=True
    )
    function_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[ExecutionStatus] = mapped_column(
        Enum(ExecutionStatus), default=ExecutionStatus.RUNNING, nullable=False
    )
    input_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    output_data: Mapped[Optional[Any]] = mapped_column(JSON)
    error: Mapped[Optional[str]] = mapped_column(Text)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Relationships
    execution: Mapped["Execution"] = relationship("Execution", back_populates="steps")
