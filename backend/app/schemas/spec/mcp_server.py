"""MCP server spec.

Config YAML (camelCase) and the REST API (snake_case) describe a server with
the same nested shape; the aliases on SpecModel translate.
"""

from typing import Any, ClassVar, Literal, Optional

from pydantic import Field, field_validator, model_validator

from app.schemas.spec.base import SpecModel

TRANSPORTS = Literal["streamable_http", "sse"]
AUTH_TYPES = Literal["none", "bearer", "header"]


class McpServerAuthSpec(SpecModel):
    # A misspelt type would send every request unauthenticated.
    type: AUTH_TYPES = "none"
    secret: Optional[str] = None  # the *name* of a Secret, never its value
    # For type "header": the header the secret's value is sent in.
    header: Optional[str] = None


class McpServerSpec(SpecModel):
    WHOLE_SPEC_FIELDS: ClassVar[frozenset[str]] = frozenset({"auth"})

    # Both become part of a model-facing function name (mcp_<ns>__<name>__…),
    # which OpenAI restricts to [A-Za-z0-9_-]; the REST identifier rule is
    # applied here so config apply can't create a server no chat can use.
    namespace: str = Field(
        default="default", min_length=1, max_length=100, pattern=r"^[a-zA-Z_][a-zA-Z0-9_-]*$"
    )
    name: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z_][a-zA-Z0-9_-]*$")
    description: Optional[str] = None
    url: str = Field(min_length=1, pattern=r"^https?://")
    transport: TRANSPORTS = "streamable_http"
    auth: McpServerAuthSpec = Field(default_factory=McpServerAuthSpec)
    headers: dict[str, str] = Field(default_factory=dict)
    # Glob patterns on the server's tool names. Empty allow = every tool;
    # deny wins over allow.
    tool_allow: list[str] = Field(default_factory=list)
    tool_deny: list[str] = Field(default_factory=list)
    # 0 timed out every call.
    timeout_seconds: int = Field(default=60, ge=1)
    connect_timeout_seconds: int = Field(default=10, ge=1)
    is_active: bool = True

    @model_validator(mode="before")
    @classmethod
    def _normalise(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("description") == "":
            data = {**data, "description": None}  # the console sends "" for none
        return data

    @field_validator("tool_allow", "tool_deny", mode="before")
    @classmethod
    def _none_is_empty(cls, value: Any) -> Any:
        return [] if value is None else value

    @property
    def key(self) -> str:
        return f"{self.namespace}/{self.name}"

    def whole_spec_problems(self) -> list[str]:
        auth = self.auth
        if auth.type in ("bearer", "header") and not auth.secret:
            return [f"auth.secret required for {auth.type} auth"]
        if auth.type == "header" and not auth.header:
            return ["auth.header required for header auth"]
        return []

    @model_validator(mode="after")
    def _whole_spec(self) -> "McpServerSpec":
        problems = self.whole_spec_problems()
        if problems:
            raise ValueError(problems[0])
        return self

    def to_config(self) -> dict[str, Any]:
        out = super().to_config()  # camelCase, None dropped (not inside dicts)
        for key in ("headers", "toolAllow", "toolDeny"):
            if not out.get(key):
                out.pop(key, None)
        return out
