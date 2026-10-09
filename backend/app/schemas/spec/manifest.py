"""Manifest spec."""

from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from app.schemas.spec.base import SpecModel


class RequiredResource(BaseModel):
    type: str
    namespace: str = "default"
    name: str


class StoreDependency(BaseModel):
    store: str  # "namespace/name"
    key: Optional[str] = None


class ManifestSpec(SpecModel):
    namespace: str = Field(default="default", min_length=1, max_length=255, pattern=r"^[^/]+$")
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None
    required_resources: list[RequiredResource] = Field(default_factory=list)
    required_permissions: list[str] = Field(default_factory=list)
    optional_permissions: list[str] = Field(default_factory=list)
    exposed_namespaces: dict[str, list[str]] = Field(default_factory=dict)
    store_dependencies: list[StoreDependency] = Field(default_factory=list)
    public_info: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True

    @field_validator("description", mode="before")
    @classmethod
    def _empty_is_none(cls, value: Any) -> Any:
        return None if value == "" else value

    @field_validator(
        "required_resources", "required_permissions", "optional_permissions", "store_dependencies",
        mode="before",
    )
    @classmethod
    def _none_is_empty_list(cls, value: Any) -> Any:
        return [] if value is None else value

    @field_validator("exposed_namespaces", mode="before")
    @classmethod
    def _none_is_empty_dict(cls, value: Any) -> Any:
        return {} if value is None else value

    @field_validator("public_info", mode="before")
    @classmethod
    def _public_info(cls, value: Any) -> dict[str, Any]:
        # Lands on the unauthenticated /info verbatim.
        from app.schemas.manifest import validate_public_info

        return validate_public_info(value)

    @property
    def key(self) -> str:
        return f"{self.namespace}/{self.name}"

    def to_config(self) -> dict[str, Any]:
        # As the export always was: empty lists and maps are left out.
        return {k: v for k, v in super().to_config().items() if v not in ({}, [])}
