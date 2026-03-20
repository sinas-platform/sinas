"""Connector schemas."""
import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class OperationConfig(BaseModel):
    """A single typed HTTP operation on a connector."""

    name: str = Field(..., min_length=1, pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    method: str = Field(..., pattern=r"^(GET|POST|PUT|PATCH|DELETE)$")
    path: str = Field(..., min_length=1)
    description: Optional[str] = None
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    request_body_mapping: str = Field(default="json", pattern=r"^(json|query|path_and_json|path_and_query)$")
    response_mapping: str = Field(default="json", pattern=r"^(json|text)$")


class ConnectorAuth(BaseModel):
    type: str = Field(default="none", pattern=r"^(bearer|basic|api_key|sinas_token|none)$")
    secret: Optional[str] = None
    header: Optional[str] = None
    position: Optional[str] = Field(default=None, pattern=r"^(header|query)$")
    param_name: Optional[str] = None


class ConnectorRetry(BaseModel):
    max_attempts: int = Field(default=1, ge=1, le=10)
    backoff: str = Field(default="none", pattern=r"^(exponential|linear|none)$")


class ConnectorCreate(BaseModel):
    namespace: str = Field(default="default", min_length=1, max_length=100, pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    name: str = Field(..., min_length=1, max_length=100, pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    description: Optional[str] = None
    base_url: str = Field(..., min_length=1)
    auth: ConnectorAuth = Field(default_factory=ConnectorAuth)
    headers: dict[str, str] = Field(default_factory=dict)
    retry: ConnectorRetry = Field(default_factory=ConnectorRetry)
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    operations: list[OperationConfig] = Field(default_factory=list)


class ConnectorUpdate(BaseModel):
    namespace: Optional[str] = Field(None, min_length=1, max_length=100, pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    name: Optional[str] = Field(None, min_length=1, max_length=100, pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    description: Optional[str] = None
    base_url: Optional[str] = None
    auth: Optional[ConnectorAuth] = None
    headers: Optional[dict[str, str]] = None
    retry: Optional[ConnectorRetry] = None
    timeout_seconds: Optional[int] = Field(None, ge=1, le=300)
    operations: Optional[list[OperationConfig]] = None
    is_active: Optional[bool] = None


class ConnectorResponse(BaseModel):
    id: uuid.UUID
    namespace: str
    name: str
    description: Optional[str]
    base_url: str
    auth: dict[str, Any]
    headers: dict[str, Any]
    retry: dict[str, Any]
    timeout_seconds: int
    operations: list[dict[str, Any]]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ConnectorTestRequest(BaseModel):
    parameters: dict[str, Any] = Field(default_factory=dict)


class ConnectorTestResponse(BaseModel):
    status_code: int
    headers: dict[str, str]
    body: Any
    elapsed_ms: float


class OpenAPIImportRequest(BaseModel):
    spec: Optional[str] = None
    spec_url: Optional[str] = None
    operations: Optional[list[str]] = None  # Filter to specific operation names
    apply: bool = False


class OpenAPIImportResponse(BaseModel):
    operations: list[OperationConfig]
    warnings: list[str] = Field(default_factory=list)
    applied: int = 0
