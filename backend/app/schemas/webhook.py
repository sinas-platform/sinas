"""Webhook schemas."""
import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from app.models.webhook import HTTPMethod


class DedupConfig(BaseModel):
    key: str = Field(..., description="JSONPath (e.g. '$.event.client_msg_id') or 'header:X-Header-Name'")
    ttl_seconds: int = Field(default=300, ge=1, le=86400)


class WebhookCreate(BaseModel):
    path: str = Field(..., min_length=1, max_length=255, pattern=r"^[a-zA-Z0-9_/-]+$")
    function_namespace: str = Field(
        default="default", min_length=1, max_length=255, pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$"
    )
    function_name: str = Field(..., min_length=1, max_length=255)
    http_method: HTTPMethod = HTTPMethod.POST
    description: Optional[str] = None
    default_values: Optional[dict[str, Any]] = None
    requires_auth: bool = True
    response_mode: Literal["sync", "async"] = "sync"
    dedup: Optional[DedupConfig] = None


class WebhookUpdate(BaseModel):
    function_namespace: Optional[str] = None
    function_name: Optional[str] = None
    http_method: Optional[HTTPMethod] = None
    description: Optional[str] = None
    default_values: Optional[dict[str, Any]] = None
    is_active: Optional[bool] = None
    requires_auth: Optional[bool] = None
    response_mode: Optional[Literal["sync", "async"]] = None
    dedup: Optional[DedupConfig] = None


class WebhookResponse(BaseModel):
    id: uuid.UUID
    path: str
    function_namespace: str
    function_name: str
    http_method: HTTPMethod
    description: Optional[str]
    default_values: Optional[dict[str, Any]]
    is_active: bool
    requires_auth: bool
    response_mode: str
    dedup: Optional[dict[str, Any]]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
