"""MCP server schemas."""
import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class MCPServerCreate(BaseModel):
    name: str
    url: str
    protocol: str = "http"  # http or websocket
    api_key: Optional[str] = None


class MCPServerUpdate(BaseModel):
    url: Optional[str] = None
    protocol: Optional[str] = None
    api_key: Optional[str] = None
    is_active: Optional[bool] = None


class MCPServerResponse(BaseModel):
    id: uuid.UUID
    name: str
    url: str
    protocol: str
    is_active: bool
    last_connected: Optional[datetime]
    connection_status: str
    error_message: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class MCPToolExecuteRequest(BaseModel):
    arguments: dict[str, Any]
