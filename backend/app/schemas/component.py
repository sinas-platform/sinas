"""Component schemas."""
import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


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
    css_overrides: Optional[str] = None
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
    css_overrides: Optional[str] = None
    visibility: Optional[str] = Field(None, pattern=r"^(private|shared|public)$")
    is_active: Optional[bool] = None
    is_published: Optional[bool] = None


class ComponentResponse(BaseModel):
    id: uuid.UUID
    user_id: Optional[uuid.UUID]
    namespace: str
    name: str
    title: Optional[str]
    description: Optional[str]
    source_code: str
    compiled_bundle: Optional[str]
    source_map: Optional[str]
    compile_status: str
    compile_errors: Optional[list[dict[str, Any]]]
    input_schema: Optional[dict[str, Any]]
    enabled_agents: list[str]
    enabled_functions: list[str]
    enabled_queries: list[str]
    enabled_components: list[str]
    enabled_stores: list[dict]
    css_overrides: Optional[str]
    visibility: str
    version: int
    is_published: bool
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
    compile_status: str
    compile_errors: Optional[list[dict[str, Any]]]
    input_schema: Optional[dict[str, Any]]
    enabled_agents: list[str]
    enabled_functions: list[str]
    enabled_queries: list[str]
    enabled_components: list[str]
    enabled_stores: list[dict]
    visibility: str
    version: int
    is_published: bool
    is_active: bool
    render_token: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ShareCreateRequest(BaseModel):
    """Request to create a share link for a component."""

    input_data: Optional[dict[str, Any]] = None
    expires_at: Optional[datetime] = None
    max_views: Optional[int] = None
    label: Optional[str] = None


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
