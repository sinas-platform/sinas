"""Component spec.

A component is an HTML page: `source_code` is its body (markup, <style>,
<script>), served as is. The `enabled_*` lists are what its code may reach
through the `sinas` client, capped by the viewer's own permissions.
"""

from typing import Any, Literal, Optional

from pydantic import Field, field_validator

from app.schemas.spec.base import SpecModel


class EnabledStoreSpec(SpecModel):
    store: str = Field(min_length=3, pattern=r"^[^/]+/.+$")  # "namespace/name"
    access: Literal["readonly", "readwrite"] = "readonly"


class ComponentSpec(SpecModel):
    namespace: str = Field(default="default", min_length=1, max_length=255, pattern=r"^[^/]+$")
    name: str = Field(min_length=1, max_length=255)
    title: Optional[str] = Field(default=None, max_length=500)
    description: Optional[str] = None
    source_code: str = Field(min_length=1)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    enabled_agents: list[str] = Field(default_factory=list)
    enabled_functions: list[str] = Field(default_factory=list)
    enabled_queries: list[str] = Field(default_factory=list)
    enabled_components: list[str] = Field(default_factory=list)
    enabled_stores: list[EnabledStoreSpec] = Field(default_factory=list)
    visibility: Literal["private", "shared", "public"] = "private"
    is_active: bool = True

    @field_validator("title", "description", mode="before")
    @classmethod
    def _empty_is_none(cls, value: Any) -> Any:
        return None if value == "" else value

    @field_validator(
        "input_schema", "enabled_agents", "enabled_functions", "enabled_queries",
        "enabled_components", "enabled_stores", mode="before",
    )
    @classmethod
    def _none_is_empty(cls, value: Any, info) -> Any:
        if value is None:
            return {} if info.field_name == "input_schema" else []
        return value

    @field_validator("enabled_stores", mode="before")
    @classmethod
    def _store_shorthand(cls, value: Any) -> Any:
        # Config has always accepted a bare "ns/name", meaning read-write.
        if isinstance(value, list):
            return [{"store": v, "access": "readwrite"} if isinstance(v, str) else v for v in value]
        return value

    @property
    def key(self) -> str:
        return f"{self.namespace}/{self.name}"
