"""Schedule schemas."""
import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, validator


class ScheduledJobCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    schedule_type: Literal["function", "agent"] = "function"
    target_namespace: str = Field(
        default="default", min_length=1, max_length=255, pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$"
    )
    target_name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    cron_expression: str = Field(..., min_length=1)
    timezone: str = "UTC"
    input_data: dict[str, Any] = Field(default_factory=dict)
    content: Optional[str] = None

    @validator("cron_expression")
    def validate_cron(cls, v):
        from croniter import croniter

        if not croniter.is_valid(v):
            raise ValueError("Invalid cron expression")
        return v

    @validator("content", always=True)
    def validate_content(cls, v, values):
        if values.get("schedule_type") == "agent" and not v:
            raise ValueError("content is required for agent schedules")
        return v


class ScheduledJobUpdate(BaseModel):
    name: Optional[str] = None
    schedule_type: Optional[Literal["function", "agent"]] = None
    target_namespace: Optional[str] = None
    target_name: Optional[str] = None
    description: Optional[str] = None
    cron_expression: Optional[str] = None
    timezone: Optional[str] = None
    input_data: Optional[dict[str, Any]] = None
    content: Optional[str] = None
    is_active: Optional[bool] = None

    @validator("cron_expression")
    def validate_cron(cls, v):
        if v is not None:
            from croniter import croniter

            if not croniter.is_valid(v):
                raise ValueError("Invalid cron expression")
        return v


class ScheduledJobResponse(BaseModel):
    id: uuid.UUID
    name: str
    schedule_type: str
    target_namespace: str
    target_name: str
    description: Optional[str]
    cron_expression: str
    timezone: str
    input_data: dict[str, Any]
    content: Optional[str]
    is_active: bool
    last_run: Optional[datetime]
    next_run: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True
