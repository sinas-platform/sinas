"""Database connection schemas."""
import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class DatabaseConnectionCreate(BaseModel):
    """Schema for creating a new database connection."""

    name: str
    connection_type: str  # postgresql, clickhouse, snowflake
    host: str
    port: int
    database: str
    username: str
    password: Optional[str] = None
    ssl_mode: Optional[str] = None
    config: Optional[dict[str, Any]] = None
    read_only: bool = False


class DatabaseConnectionUpdate(BaseModel):
    """Schema for updating a database connection."""

    name: Optional[str] = None
    connection_type: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    database: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    ssl_mode: Optional[str] = None
    config: Optional[dict[str, Any]] = None
    is_active: Optional[bool] = None
    read_only: Optional[bool] = None


class DatabaseConnectionResponse(BaseModel):
    """Schema for database connection response (password excluded)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    connection_type: str
    host: str
    port: int
    database: str
    username: str
    ssl_mode: Optional[str] = None
    config: dict[str, Any]
    is_active: bool
    read_only: bool
    created_at: datetime
    updated_at: datetime


class DatabaseConnectionTestRequest(BaseModel):
    """Schema for testing a connection with raw params (before saving)."""

    connection_type: str
    host: str
    port: int
    database: str
    username: str
    password: Optional[str] = None
    ssl_mode: Optional[str] = None


class DatabaseConnectionTestResponse(BaseModel):
    """Response from testing a database connection."""

    success: bool
    message: str
    latency_ms: Optional[int] = None
