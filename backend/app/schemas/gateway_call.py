"""Gateway audit log responses."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class GatewayCallResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    manifest_id: uuid.UUID | None
    manifest_namespace: str
    manifest_name: str
    user_id: uuid.UUID
    api_key_id: uuid.UUID | None
    tool_name: str
    resource_type: str
    resource_ref: str
    arguments: dict[str, Any] | None
    result: dict[str, Any] | None
    is_error: bool
    duration_ms: int | None
    created_at: datetime


class GatewayCallListResponse(BaseModel):
    items: list[GatewayCallResponse]
    total: int
    limit: int
    offset: int
