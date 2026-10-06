"""Connector spec.

Config YAML and the REST API describe a connector with the same nested shape
(camelCase in YAML, snake_case in the API and in storage); the aliases on
SpecModel translate, replacing the hand-kept field maps that each new auth
field had to be added to in four places.
"""

from typing import Any, ClassVar, Literal, Optional

from pydantic import Field, field_validator, model_validator

from app.schemas.spec.base import SpecModel

AUTH_TYPES = Literal[
    "none", "bearer", "basic", "api_key", "sinas_token",
    "oauth2_client_credentials", "oauth2_authorization_code",
]
REQUEST_BODY_MAPPINGS = Literal["json", "query", "path_and_json", "path_and_query"]


class ConnectorOperationSpec(SpecModel):
    name: str = Field(min_length=1)
    # Any HTTP method token, any case: config passed the string straight to
    # the HTTP client, so HEAD, TRACE or a custom method (PROPFIND, ...) worked.
    # Normalised to upper case, as the client sends it.
    method: str = Field(pattern=r"^[A-Z][A-Z0-9_-]*$", max_length=32)
    path: str = Field(min_length=1)
    description: Optional[str] = None
    parameters: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )
    # An unknown mapping silently dropped every parameter from the request.
    request_body_mapping: REQUEST_BODY_MAPPINGS = "json"
    # Anything but "json" has always meant text.
    response_mapping: str = "json"

    @field_validator("method", mode="before")
    @classmethod
    def _upper(cls, value: Any) -> Any:
        return value.upper() if isinstance(value, str) else value


class TokenResponsePathsSpec(SpecModel):
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    expires_in: Optional[str] = None
    scope: Optional[str] = None
    success_flag: Optional[str] = None
    error: Optional[str] = None
    error_description: Optional[str] = None


class ConnectorAuthSpec(SpecModel):
    # A misspelt type ("oauth2", "Bearer") sent every request unauthenticated.
    type: AUTH_TYPES = "none"
    secret: Optional[str] = None  # the *name* of a Secret, never its value
    header: Optional[str] = None
    # Unknown values have always meant "header"; the client lower-cases it.
    position: Optional[str] = None
    param_name: Optional[str] = None
    token_url: Optional[str] = None
    client_id: Optional[str] = None
    scopes: Optional[list[str]] = None
    authorize_url: Optional[str] = None
    # Unknown values have always meant "body".
    client_auth_method: Optional[str] = None
    token_params: Optional[dict[str, str]] = None
    token_response_paths: Optional[TokenResponsePathsSpec] = None

    @field_validator("token_response_paths", mode="before")
    @classmethod
    def _empty_paths_are_none(cls, value: Any) -> Any:
        # {} (or all nulls) means "standard response": store nothing, or every
        # apply would see {} vs None as a change.
        if isinstance(value, dict) and not any(value.values()):
            return None
        if isinstance(value, TokenResponsePathsSpec) and not any(value.model_dump().values()):
            return None
        return value


class ConnectorRetrySpec(SpecModel):
    # 0 attempts made every call fail with a TypeError. No upper bound:
    # config never had one (the REST schema's 10 stays there).
    max_attempts: int = Field(default=1, ge=1)
    # Unknown values have always meant "none".
    backoff: str = "none"


class ConnectorSpec(SpecModel):
    WHOLE_SPEC_FIELDS: ClassVar[frozenset[str]] = frozenset({"auth"})

    # No "/": every reference ("ns/name" in agents, pipelines, the resource
    # key) splits on the first one, so such a namespace was never reachable.
    namespace: str = Field(default="default", min_length=1, max_length=100, pattern=r"^[^/]+$")
    name: str = Field(min_length=1, max_length=100)
    description: Optional[str] = None
    base_url: str = Field(min_length=1)
    auth: ConnectorAuthSpec = Field(default_factory=ConnectorAuthSpec)
    headers: dict[str, str] = Field(default_factory=dict)
    retry: ConnectorRetrySpec = Field(default_factory=ConnectorRetrySpec)
    # 0 timed out every call.
    timeout_seconds: int = Field(default=30, ge=1)
    operations: list[ConnectorOperationSpec] = Field(default_factory=list)
    is_active: bool = True

    @model_validator(mode="before")
    @classmethod
    def _normalise(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("description") == "":
            data = {**data, "description": None}  # the console sends "" for none
        return data

    @property
    def key(self) -> str:
        return f"{self.namespace}/{self.name}"

    def whole_spec_problems(self) -> list[str]:
        """OAuth grants that can't work without these (they failed at the
        first call, or the first sign-in)."""
        auth = self.auth
        required = {
            "oauth2_client_credentials": ("token_url", "client_id"),
            "oauth2_authorization_code": ("authorize_url", "token_url", "client_id"),
        }.get(auth.type, ())
        missing = [field for field in required if not getattr(auth, field, None)]
        if missing:
            return [f"auth.{', auth.'.join(missing)} required for {auth.type} connectors"]
        return []

    @model_validator(mode="after")
    def _whole_spec(self) -> "ConnectorSpec":
        problems = self.whole_spec_problems()
        if problems:
            raise ValueError(problems[0])
        return self

    def to_config(self) -> dict[str, Any]:
        out = super().to_config()  # camelCase, None dropped (not inside dicts)
        if not out.get("headers"):
            out.pop("headers", None)
        return out
