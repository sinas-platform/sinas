"""Manifest registration schemas."""
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class ResourceRef(BaseModel):
    """Reference to a SINAS resource."""

    type: str = Field(..., description="Resource type: agent, function, skill, collection")
    namespace: str = Field(default="default", description="Resource namespace")
    name: str = Field(..., description="Resource name")


class StoreDependency(BaseModel):
    """Expected store (and optional key) that a manifest depends on."""

    store: str = Field(..., description="Store reference in format 'namespace/name'")
    key: Optional[str] = Field(None, description="Optional specific key within store")


class ManifestCreate(BaseModel):
    namespace: str = Field(
        default="default", min_length=1, max_length=255, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$"
    )
    name: str = Field(..., min_length=1, max_length=255, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$")
    description: Optional[str] = None
    required_resources: list[ResourceRef] = Field(default_factory=list)
    required_permissions: list[str] = Field(default_factory=list)
    optional_permissions: list[str] = Field(default_factory=list)
    exposed_namespaces: dict[str, list[str]] = Field(default_factory=dict)
    store_dependencies: list[StoreDependency] = Field(default_factory=list)

    @field_validator("exposed_namespaces")
    @classmethod
    def validate_exposed_namespace_keys(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        allowed = {"agents", "functions", "skills", "templates", "collections", "components", "stores"}
        invalid = set(v.keys()) - allowed
        if invalid:
            raise ValueError(f"Invalid exposed_namespaces keys: {invalid}. Allowed: {allowed}")
        return v


class ManifestUpdate(BaseModel):
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
    store_dependencies: Optional[list[StoreDependency]] = None
    is_active: Optional[bool] = None

    @field_validator("exposed_namespaces")
    @classmethod
    def validate_exposed_namespace_keys(cls, v: dict[str, list[str]] | None) -> dict[str, list[str]] | None:
        if v is None:
            return v
        allowed = {"agents", "functions", "skills", "templates", "collections", "components", "stores"}
        invalid = set(v.keys()) - allowed
        if invalid:
            raise ValueError(f"Invalid exposed_namespaces keys: {invalid}. Allowed: {allowed}")
        return v


class ManifestResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    namespace: str
    name: str
    description: Optional[str]
    required_resources: list[ResourceRef]
    required_permissions: list[str]
    optional_permissions: list[str]
    exposed_namespaces: dict[str, list[str]]
    store_dependencies: list[StoreDependency]
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
    """Status of permissions for the manifest."""

    granted: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


class StoreDependencyStatus(BaseModel):
    """Status of a single store dependency."""

    store: str
    key: Optional[str] = None
    exists: bool


class ManifestStatusResponse(BaseModel):
    """Validation result for a manifest's dependencies."""

    ready: bool
    resources: dict[str, list[ResourceStatus]] = Field(
        default_factory=lambda: {"satisfied": [], "missing": []}
    )
    permissions: dict[str, PermissionStatus] = Field(
        default_factory=lambda: {"required": PermissionStatus(), "optional": PermissionStatus()}
    )
    stores: dict[str, list[StoreDependencyStatus]] = Field(
        default_factory=lambda: {"satisfied": [], "missing": []}
    )
