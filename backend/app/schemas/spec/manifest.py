"""Manifest spec."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

from app.schemas.spec.base import SpecModel


class RequiredResource(BaseModel):
    type: str
    namespace: str = "default"
    name: str


class StoreDependency(BaseModel):
    store: str  # "namespace/name"
    key: str | None = None


GATEWAY_TOOL_TYPES = ("function", "query", "pipeline")
_TOOL_NAME = r"^[a-zA-Z0-9_-]{1,64}$"


class ExposedTool(BaseModel):
    """One resource the manifest serves as an MCP tool through the gateway.

    `tool_name` is the name the agent calls it by (default: `namespace__name`);
    `description` overrides the resource's own description for the agent.
    Accepts the config spelling (`toolName`) and the REST one (`tool_name`)."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    type: str
    namespace: str = "default"
    name: str
    tool_name: str | None = Field(default=None, pattern=_TOOL_NAME)
    description: str | None = None

    @field_validator("type")
    @classmethod
    def _known_type(cls, value: str) -> str:
        if value not in GATEWAY_TOOL_TYPES:
            raise ValueError(
                f"exposed tool type must be one of {', '.join(GATEWAY_TOOL_TYPES)}, got {value!r}"
            )
        return value

    @property
    def ref(self) -> str:
        return f"{self.namespace}/{self.name}"


class GatewayLimits(BaseModel):
    """Per-caller rate limit on the manifest's gateway tool calls."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    max_calls: int = Field(ge=0, description="Calls per caller per window; 0 disables the limit")
    window_seconds: int = Field(default=60, ge=1, le=86400)


class ManifestSpec(SpecModel):
    namespace: str = Field(default="default", min_length=1, max_length=255, pattern=r"^[^/]+$")
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    required_resources: list[RequiredResource] = Field(default_factory=list)
    required_permissions: list[str] = Field(default_factory=list)
    optional_permissions: list[str] = Field(default_factory=list)
    exposed_namespaces: dict[str, list[str]] = Field(default_factory=dict)
    store_dependencies: list[StoreDependency] = Field(default_factory=list)
    public_info: dict[str, Any] = Field(default_factory=dict)
    exposed_tools: list[ExposedTool] = Field(default_factory=list)
    gateway_limits: GatewayLimits | None = None
    is_active: bool = True

    @field_validator("description", mode="before")
    @classmethod
    def _empty_is_none(cls, value: Any) -> Any:
        return None if value == "" else value

    @field_validator(
        "required_resources", "required_permissions", "optional_permissions", "store_dependencies",
        "exposed_tools",
        mode="before",
    )
    @classmethod
    def _none_is_empty_list(cls, value: Any) -> Any:
        return [] if value is None else value

    @field_validator("exposed_tools")
    @classmethod
    def _tool_names_unique(cls, value: list[ExposedTool]) -> list[ExposedTool]:
        from app.services.gateway import default_tool_name

        seen: dict[str, str] = {}
        for tool in value:
            name = tool.tool_name or default_tool_name(tool.namespace, tool.name)
            other = seen.get(name)
            if other is not None and other != f"{tool.type}:{tool.ref}":
                raise ValueError(
                    f"exposed tools '{other}' and '{tool.type}:{tool.ref}' would both be called "
                    f"'{name}'; give one a toolName"
                )
            seen[name] = f"{tool.type}:{tool.ref}"
        return value

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
