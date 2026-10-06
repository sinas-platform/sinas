"""Manifest registration schemas."""
import json
import re
import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

PUBLIC_INFO_MAX_BYTES = 4096
_PUBLIC_INFO_KEY = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*$")


def validate_public_info(value: Any) -> dict[str, Any]:
    """public_info lands on the unauthenticated /info verbatim, so keep it a
    small, plain JSON object with identifier keys. Values may nest."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("public_info must be a JSON object")
    for key in value:
        if not isinstance(key, str) or not _PUBLIC_INFO_KEY.match(key):
            raise ValueError(f"public_info key {key!r} must be an identifier (letters, digits, underscores)")
    try:
        encoded = json.dumps(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"public_info must be JSON-serialisable: {exc}") from exc
    if len(encoded.encode()) > PUBLIC_INFO_MAX_BYTES:
        raise ValueError(f"public_info must be at most {PUBLIC_INFO_MAX_BYTES} bytes as JSON")
    return value


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
    public_info: dict[str, Any] = Field(
        default_factory=dict,
        description="Published on the unauthenticated GET /info under services[namespace]",
    )

    @field_validator("public_info")
    @classmethod
    def _validate_public_info(cls, v: dict[str, Any]) -> dict[str, Any]:
        return validate_public_info(v)

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
    public_info: dict[str, Any] | None = None
    is_active: Optional[bool] = None

    @field_validator("public_info")
    @classmethod
    def _validate_public_info(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        return None if v is None else validate_public_info(v)

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
    public_info: dict[str, Any] = Field(default_factory=dict)
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
