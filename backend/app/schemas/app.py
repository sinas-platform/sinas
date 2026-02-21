"""App registration schemas."""
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class ResourceRef(BaseModel):
    """Reference to a SINAS resource."""

    type: str = Field(..., description="Resource type: agent, function, skill, collection")
    namespace: str = Field(default="default", description="Resource namespace")
    name: str = Field(..., description="Resource name")


class AppCreate(BaseModel):
    namespace: str = Field(
        default="default", min_length=1, max_length=255, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$"
    )
    name: str = Field(..., min_length=1, max_length=255, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$")
    description: Optional[str] = None
    required_resources: list[ResourceRef] = Field(default_factory=list)
    required_permissions: list[str] = Field(default_factory=list)
    optional_permissions: list[str] = Field(default_factory=list)
    exposed_namespaces: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("exposed_namespaces")
    @classmethod
    def validate_exposed_namespace_keys(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        allowed = {"agents", "functions", "skills", "templates", "collections"}
        invalid = set(v.keys()) - allowed
        if invalid:
            raise ValueError(f"Invalid exposed_namespaces keys: {invalid}. Allowed: {allowed}")
        return v


class AppUpdate(BaseModel):
    namespace: Optional[str] = Field(
        None, min_length=1, max_length=255, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$"
    )
    name: Optional[str] = Field(
        None, min_length=1, max_length=255, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$"
    )
    description: Optional[str] = None
    required_resources: Optional[list[ResourceRef]] = None
    required_permissions: Optional[list[str]] = None
    optional_permissions: Optional[list[str]] = None
    exposed_namespaces: Optional[dict[str, list[str]]] = None
    is_active: Optional[bool] = None

    @field_validator("exposed_namespaces")
    @classmethod
    def validate_exposed_namespace_keys(cls, v: dict[str, list[str]] | None) -> dict[str, list[str]] | None:
        if v is None:
            return v
        allowed = {"agents", "functions", "skills", "templates", "collections"}
        invalid = set(v.keys()) - allowed
        if invalid:
            raise ValueError(f"Invalid exposed_namespaces keys: {invalid}. Allowed: {allowed}")
        return v


class AppResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    namespace: str
    name: str
    description: Optional[str]
    required_resources: list[ResourceRef]
    required_permissions: list[str]
    optional_permissions: list[str]
    exposed_namespaces: dict[str, list[str]]
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


class ResourceStatus(BaseModel):
    """Status of a single resource reference."""

    type: str
    namespace: str
    name: str
    exists: bool


class PermissionStatus(BaseModel):
    """Status of permissions for the app."""

    granted: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


class AppStatusResponse(BaseModel):
    """Validation result for an app's dependencies."""

    ready: bool
    resources: dict[str, list[ResourceStatus]] = Field(
        default_factory=lambda: {"satisfied": [], "missing": []}
    )
    permissions: dict[str, PermissionStatus] = Field(
        default_factory=lambda: {"required": PermissionStatus(), "optional": PermissionStatus()}
    )
