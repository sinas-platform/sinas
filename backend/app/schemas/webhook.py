"""Webhook schemas."""
import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.models.webhook import HTTPMethod


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


class WebhookUpdate(BaseModel):
    function_namespace: Optional[str] = None
    function_name: Optional[str] = None
    http_method: Optional[HTTPMethod] = None
    description: Optional[str] = None
    default_values: Optional[dict[str, Any]] = None
    is_active: Optional[bool] = None
    requires_auth: Optional[bool] = None


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
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
