"""MCP server API schemas."""
import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class McpServerAuth(BaseModel):
    type: str = "none"  # none | bearer | header
    secret: Optional[str] = None  # name of a Secret, never its value
    header: Optional[str] = None  # for type "header"


class McpServerCreate(BaseModel):
    namespace: str = Field(default="default", min_length=1, max_length=100, pattern=r"^[a-zA-Z_][a-zA-Z0-9_-]*$")
    name: str = Field(..., min_length=1, max_length=100, pattern=r"^[a-zA-Z_][a-zA-Z0-9_-]*$")
    description: Optional[str] = None
    url: str = Field(..., min_length=1)
    transport: str = "streamable_http"
    auth: McpServerAuth = Field(default_factory=McpServerAuth)
    headers: dict[str, str] = Field(default_factory=dict)
    tool_allow: list[str] = Field(default_factory=list)
    tool_deny: list[str] = Field(default_factory=list)
    timeout_seconds: int = Field(default=60, ge=1, le=600)
    connect_timeout_seconds: int = Field(default=10, ge=1, le=120)


class McpServerUpdate(BaseModel):
    namespace: Optional[str] = Field(None, min_length=1, max_length=100, pattern=r"^[a-zA-Z_][a-zA-Z0-9_-]*$")
    name: Optional[str] = Field(None, min_length=1, max_length=100, pattern=r"^[a-zA-Z_][a-zA-Z0-9_-]*$")
    description: Optional[str] = None
    url: Optional[str] = None
    transport: Optional[str] = None
    auth: Optional[McpServerAuth] = None
    headers: Optional[dict[str, str]] = None
    tool_allow: Optional[list[str]] = None
    tool_deny: Optional[list[str]] = None
    timeout_seconds: Optional[int] = Field(None, ge=1, le=600)
    connect_timeout_seconds: Optional[int] = Field(None, ge=1, le=120)
    is_active: Optional[bool] = None


class McpServerResponse(BaseModel):
    id: uuid.UUID
    namespace: str
    name: str
    description: Optional[str]
    url: str
    transport: str
    auth: dict[str, Any]
    headers: dict[str, Any]
    tool_allow: list[str]
    tool_deny: list[str]
    timeout_seconds: int
    connect_timeout_seconds: int
    is_active: bool
    managed_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class McpServerToolInfo(BaseModel):
    name: str
    title: Optional[str] = None
    description: Optional[str] = None
    input_schema: dict[str, Any] = Field(default_factory=dict)
    annotations: Optional[dict[str, Any]] = None
    # False when the server's allow/deny lists hide it from agents.
    allowed: bool = True


class McpServerToolsResponse(BaseModel):
    """What a live tools/list returned — the console's "test connection"."""
    tools: list[McpServerToolInfo]
    elapsed_ms: float
