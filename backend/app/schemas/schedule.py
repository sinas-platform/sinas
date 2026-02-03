"""Schedule schemas."""
import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, validator


class ScheduledJobCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    function_namespace: str = Field(
        default="default", min_length=1, max_length=255, pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$"
    )
    function_name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    cron_expression: str = Field(..., min_length=1)
    timezone: str = "UTC"
    input_data: dict[str, Any]

    @validator("cron_expression")
    def validate_cron(cls, v):
        from croniter import croniter

        if not croniter.is_valid(v):
            raise ValueError("Invalid cron expression")
        return v


class ScheduledJobUpdate(BaseModel):
    function_namespace: Optional[str] = None
    function_name: Optional[str] = None
    description: Optional[str] = None
    cron_expression: Optional[str] = None
    timezone: Optional[str] = None
    input_data: Optional[dict[str, Any]] = None
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
    function_namespace: str
    function_name: str
    description: Optional[str]
    cron_expression: str
    timezone: str
    input_data: dict[str, Any]
    is_active: bool
    last_run: Optional[datetime]
    next_run: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True
