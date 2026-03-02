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
    state_namespaces_readonly: Optional[list[str]] = None
    state_namespaces_readwrite: Optional[list[str]] = None
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
    state_namespaces_readonly: Optional[list[str]] = None
    state_namespaces_readwrite: Optional[list[str]] = None
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
    state_namespaces_readonly: list[str]
    state_namespaces_readwrite: list[str]
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
    state_namespaces_readonly: list[str]
    state_namespaces_readwrite: list[str]
    visibility: str
    version: int
    is_published: bool
    is_active: bool
    render_token: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
