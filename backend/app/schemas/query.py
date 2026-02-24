"""Query schemas."""
import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class QueryCreate(BaseModel):
    namespace: str = Field(
        default="default", min_length=1, max_length=255, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$"
    )
    name: str = Field(..., min_length=1, max_length=255, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$")
    description: Optional[str] = None
    database_connection_id: uuid.UUID
    operation: str = Field(..., pattern=r"^(read|write)$")
    sql: str = Field(..., min_length=1)
    input_schema: Optional[dict[str, Any]] = None
    output_schema: Optional[dict[str, Any]] = None
    timeout_ms: int = 5000
    max_rows: int = 1000


class QueryUpdate(BaseModel):
    namespace: Optional[str] = Field(
        None, min_length=1, max_length=255, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$"
    )
    name: Optional[str] = Field(
        None, min_length=1, max_length=255, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$"
    )
    description: Optional[str] = None
    database_connection_id: Optional[uuid.UUID] = None
    operation: Optional[str] = Field(None, pattern=r"^(read|write)$")
    sql: Optional[str] = Field(None, min_length=1)
    input_schema: Optional[dict[str, Any]] = None
    output_schema: Optional[dict[str, Any]] = None
    timeout_ms: Optional[int] = None
    max_rows: Optional[int] = None
    is_active: Optional[bool] = None


class QueryResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    namespace: str
    name: str
    description: Optional[str]
    database_connection_id: uuid.UUID
    operation: str
    sql: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    timeout_ms: int
    max_rows: int
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


class QueryExecuteRequest(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)


class QueryExecuteResponse(BaseModel):
    success: bool
    operation: str
    data: Optional[list[dict[str, Any]]] = None  # rows for reads
    row_count: Optional[int] = None
    affected_rows: Optional[int] = None  # for writes
    duration_ms: int
