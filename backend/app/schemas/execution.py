"""Execution schemas."""
import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel

from app.models.execution import ExecutionStatus, TriggerType


class ExecutionResponse(BaseModel):
    id: uuid.UUID
    execution_id: str
    function_name: str
    trigger_type: TriggerType
    trigger_id: uuid.UUID
    status: ExecutionStatus
    input_data: dict[str, Any]
    output_data: Optional[Any]
    error: Optional[str]
    traceback: Optional[str]
    started_at: datetime
    completed_at: Optional[datetime]
    duration_ms: Optional[int]

    class Config:
        from_attributes = True


class StepExecutionResponse(BaseModel):
    id: uuid.UUID
    execution_id: str
    function_name: str
    status: ExecutionStatus
    input_data: dict[str, Any]
    output_data: Optional[Any]
    error: Optional[str]
    duration_ms: Optional[int]
    started_at: datetime
    completed_at: Optional[datetime]

    class Config:
        from_attributes = True


class ContinueExecutionRequest(BaseModel):
    input: dict[str, Any]


class ContinueExecutionResponse(BaseModel):
    execution_id: str
    status: ExecutionStatus
    output_data: Optional[Any] = None
    prompt: Optional[str] = None
    schema: Optional[dict[str, Any]] = None
