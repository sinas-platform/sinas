"""Component schemas."""
import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class ComponentCreate(BaseModel):
    namespace: str = Field(
        default="default", min_length=1, max_length=255, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$"
    )
    name: str = Field(..., min_length=1, max_length=255, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$")
    title: Optional[str] = Field(None, max_length=500)
    description: Optional[str] = None
    source_code: str = Field(..., min_length=1)
    input_schema: Optional[dict[str, Any]] = None
    enabled_agents: Optional[list[str]] = None
    enabled_functions: Optional[list[str]] = None
    enabled_queries: Optional[list[str]] = None
    enabled_components: Optional[list[str]] = None
    enabled_stores: Optional[list[dict]] = None  # [{"store": "ns/name", "access": "readonly|readwrite"}]
    visibility: str = Field(default="private", pattern=r"^(private|shared|public)$")


class ComponentUpdate(BaseModel):
    namespace: Optional[str] = Field(
        None, min_length=1, max_length=255, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$"
    )
    name: Optional[str] = Field(
        None, min_length=1, max_length=255, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$"
    )
    title: Optional[str] = Field(None, max_length=500)
    description: Optional[str] = None
    source_code: Optional[str] = Field(None, min_length=1)
    input_schema: Optional[dict[str, Any]] = None
    enabled_agents: Optional[list[str]] = None
    enabled_functions: Optional[list[str]] = None
    enabled_queries: Optional[list[str]] = None
    enabled_components: Optional[list[str]] = None
    enabled_stores: Optional[list[dict]] = None  # [{"store": "ns/name", "access": "readonly|readwrite"}]
    visibility: Optional[str] = Field(None, pattern=r"^(private|shared|public)$")
    is_active: Optional[bool] = None


class ComponentResponse(BaseModel):
    id: uuid.UUID
    user_id: Optional[uuid.UUID]
    namespace: str
    name: str
    title: Optional[str]
    description: Optional[str]
    source_code: str
    input_schema: Optional[dict[str, Any]]
    enabled_agents: list[str]
    enabled_functions: list[str]
    enabled_queries: list[str]
    enabled_components: list[str]
    enabled_stores: list[dict]
    visibility: str
    is_active: bool
    render_token: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ComponentListResponse(BaseModel):
    """Response for list endpoints - excludes large fields."""

    id: uuid.UUID
    user_id: Optional[uuid.UUID]
    namespace: str
    name: str
    title: Optional[str]
    description: Optional[str]
    input_schema: Optional[dict[str, Any]]
    enabled_agents: list[str]
    enabled_functions: list[str]
    enabled_queries: list[str]
    enabled_components: list[str]
    enabled_stores: list[dict]
    visibility: str
    is_active: bool
    render_token: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ShareCreateRequest(BaseModel):
    """Request to create a share link for a component.

    mode: "snapshot" (fixed inputs, no live access), "viewer" (signed-in
    users, their own permissions) or "creator" (anyone with the link, the
    creator's permissions; read only unless allow_writes)."""

    input_data: Optional[dict[str, Any]] = None
    expires_at: Optional[datetime] = None
    max_views: Optional[int] = Field(None, ge=1)
    label: Optional[str] = Field(None, max_length=255)
    mode: Literal["snapshot", "viewer", "creator"] = "snapshot"
    allow_writes: bool = False

    @model_validator(mode="after")
    def _writes_only_as_creator(self) -> "ShareCreateRequest":
        if self.allow_writes and self.mode != "creator":
            raise ValueError("allow_writes applies to creator links only")
        return self


class ShareResponse(BaseModel):
    """Response for a share link."""

    id: str
    token: str
    component_id: str
    input_data: Optional[dict[str, Any]]
    expires_at: Optional[datetime]
    max_views: Optional[int]
    view_count: int
    label: Optional[str]
    mode: str
    allow_writes: bool
    created_at: datetime
    share_url: str

    class Config:
        from_attributes = True


class ProxyExecuteRequest(BaseModel):
    """Request to execute a function through the component proxy."""

    input: dict[str, Any] = {}
    timeout: Optional[int] = None


class StateProxyRequest(BaseModel):
    """Request to access state through the component proxy."""

    action: str  # get, set, delete, list
    key: Optional[str] = None
    value: Optional[dict[str, Any]] = None
    visibility: str = "private"
