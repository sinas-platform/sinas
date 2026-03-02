"""Database trigger schemas for CDC."""
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, validator


VALID_OPERATIONS = {"INSERT", "UPDATE"}


class DatabaseTriggerCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    database_connection_id: uuid.UUID
    schema_name: str = Field(default="public", max_length=255)
    table_name: str = Field(..., min_length=1, max_length=255)
    operations: list[str] = Field(default=["INSERT", "UPDATE"])
    function_namespace: str = Field(default="default", min_length=1, max_length=255)
    function_name: str = Field(..., min_length=1, max_length=255)
    poll_column: str = Field(..., min_length=1, max_length=255)
    poll_interval_seconds: int = Field(default=10, ge=1, le=3600)
    batch_size: int = Field(default=100, ge=1, le=10000)

    @validator("operations")
    def validate_operations(cls, v):
        invalid = set(v) - VALID_OPERATIONS
        if invalid:
            raise ValueError(f"Invalid operations: {invalid}. Must be subset of {VALID_OPERATIONS}")
        if not v:
            raise ValueError("At least one operation is required")
        return v


class DatabaseTriggerUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    operations: Optional[list[str]] = None
    function_namespace: Optional[str] = Field(default=None, min_length=1, max_length=255)
    function_name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    poll_column: Optional[str] = Field(default=None, min_length=1, max_length=255)
    poll_interval_seconds: Optional[int] = Field(default=None, ge=1, le=3600)
    batch_size: Optional[int] = Field(default=None, ge=1, le=10000)
    is_active: Optional[bool] = None

    @validator("operations")
    def validate_operations(cls, v):
        if v is not None:
            invalid = set(v) - VALID_OPERATIONS
            if invalid:
                raise ValueError(f"Invalid operations: {invalid}. Must be subset of {VALID_OPERATIONS}")
            if not v:
                raise ValueError("At least one operation is required")
        return v


class DatabaseTriggerResponse(BaseModel):
    id: uuid.UUID
    name: str
    database_connection_id: uuid.UUID
    schema_name: str
    table_name: str
    operations: list[str]
    function_namespace: str
    function_name: str
    poll_column: str
    poll_interval_seconds: int
    batch_size: int
    is_active: bool
    last_poll_value: Optional[str]
    error_message: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
