"""Webhook schemas."""
import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from app.models.webhook import HTTPMethod


class DedupConfig(BaseModel):
    key: str = Field(..., description="JSONPath (e.g. '$.event.client_msg_id') or 'header:X-Header-Name'")
    ttl_seconds: int = Field(default=300, ge=1, le=86400)


class WebhookCreate(BaseModel):
    path: str = Field(..., min_length=1, max_length=255, pattern=r"^[a-zA-Z0-9_/-]+$")
    target_type: Literal["function", "agent", "pipeline"] = "function"
    function_namespace: str = Field(
        default="default", min_length=1, max_length=255, pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$"
    )
    function_name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    agent_namespace: str = Field(
        default="default", min_length=1, max_length=255, pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$"
    )
    agent_name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    pipeline_namespace: str = Field(
        default="default", min_length=1, max_length=255, pattern=r"^[a-zA-Z_][a-zA-Z0-9_-]*$"
    )
    pipeline_name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    message_template: Optional[str] = None
    session_key_template: Optional[str] = Field(default=None, max_length=500)
    http_method: HTTPMethod = HTTPMethod.POST
    description: Optional[str] = None
    default_values: Optional[dict[str, Any]] = None
    requires_auth: bool = True
    response_mode: Literal["sync", "async", "raw"] = "sync"
    dedup: Optional[DedupConfig] = None

    @model_validator(mode="after")
    def validate_target(self):
        if self.target_type == "function":
            if not self.function_name:
                raise ValueError("function_name is required for function-target webhooks")
        elif self.target_type == "pipeline":
            if not self.pipeline_name:
                raise ValueError("pipeline_name is required for pipeline-target webhooks")
            if self.response_mode == "raw":
                raise ValueError("response_mode 'raw' is only supported for function-target webhooks")
        else:  # agent
            if not self.agent_name:
                raise ValueError("agent_name is required for agent-target webhooks")
            if not self.message_template:
                raise ValueError("message_template is required for agent-target webhooks")
            if self.response_mode == "raw":
                raise ValueError("response_mode 'raw' is only supported for function-target webhooks")
        return self


class WebhookUpdate(BaseModel):
    target_type: Optional[Literal["function", "agent", "pipeline"]] = None
    function_namespace: Optional[str] = None
    function_name: Optional[str] = None
    agent_namespace: Optional[str] = None
    agent_name: Optional[str] = None
    pipeline_namespace: Optional[str] = None
    pipeline_name: Optional[str] = None
    message_template: Optional[str] = None
    session_key_template: Optional[str] = None
    http_method: Optional[HTTPMethod] = None
    description: Optional[str] = None
    default_values: Optional[dict[str, Any]] = None
    is_active: Optional[bool] = None
    requires_auth: Optional[bool] = None
    response_mode: Optional[Literal["sync", "async", "raw"]] = None
    dedup: Optional[DedupConfig] = None


class WebhookResponse(BaseModel):
    id: uuid.UUID
    path: str
    target_type: str
    function_namespace: str
    function_name: Optional[str]
    agent_namespace: Optional[str]
    agent_name: Optional[str]
    pipeline_namespace: Optional[str]
    pipeline_name: Optional[str]
    message_template: Optional[str]
    session_key_template: Optional[str]
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
