"""Connector schemas."""
import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class OperationConfig(BaseModel):
    """A single typed HTTP operation on a connector."""

    name: str = Field(..., min_length=1, pattern=r"^[a-zA-Z_][a-zA-Z0-9_-]*$")
    method: str = Field(..., pattern=r"^(GET|POST|PUT|PATCH|DELETE)$")
    path: str = Field(..., min_length=1)
    description: Optional[str] = None
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    request_body_mapping: str = Field(default="json", pattern=r"^(json|query|path_and_json|path_and_query)$")
    response_mapping: str = Field(default="json", pattern=r"^(json|text)$")


class TokenResponsePaths(BaseModel):
    """Dot-paths into a nonstandard OAuth token response (issue #109).

    All optional; defaults reproduce standard RFC 6749 behavior exactly.
    Example (Slack user tokens): access_token="authed_user.access_token",
    success_flag="ok", error="error".
    """

    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    expires_in: Optional[str] = None
    scope: Optional[str] = None
    # If set, this path must be truthy or the exchange is treated as failed
    # (covers providers that signal errors on HTTP 200).
    success_flag: Optional[str] = None
    error: Optional[str] = None
    error_description: Optional[str] = None


class ConnectorAuth(BaseModel):
    type: str = Field(
        default="none",
        pattern=r"^(bearer|basic|api_key|sinas_token|oauth2_client_credentials|oauth2_authorization_code|none)$",
    )
    secret: Optional[str] = None
    header: Optional[str] = None
    position: Optional[str] = Field(default=None, pattern=r"^(header|query)$")
    param_name: Optional[str] = None

    # OAuth 2.0 grants (client-credentials and authorization-code).
    # `secret` holds the name of the Secret containing the client secret.
    token_url: Optional[str] = None
    client_id: Optional[str] = None
    scopes: Optional[list[str]] = None
    # Authorization-code grant only (type="oauth2_authorization_code"): the provider's
    # authorization endpoint the user's browser is redirected to. Tokens are stored
    # per-user (see ConnectorOAuthToken) rather than shared.
    authorize_url: Optional[str] = None
    # How to present client credentials to the token endpoint:
    # "basic" = HTTP Basic (client_secret_basic), "body" = form fields (client_secret_post).
    client_auth_method: Optional[str] = Field(default=None, pattern=r"^(basic|body)$")
    # Extra params sent with the token request (e.g. {"audience": "..."}).
    token_params: Optional[dict[str, str]] = None
    # Where to find token fields / success in a nonstandard token response.
    token_response_paths: Optional[TokenResponsePaths] = None


class ConnectorRetry(BaseModel):
    max_attempts: int = Field(default=1, ge=1, le=10)
    backoff: str = Field(default="none", pattern=r"^(exponential|linear|none)$")


class ConnectorCreate(BaseModel):
    namespace: str = Field(default="default", min_length=1, max_length=100, pattern=r"^[a-zA-Z_][a-zA-Z0-9_-]*$")
    name: str = Field(..., min_length=1, max_length=100, pattern=r"^[a-zA-Z_][a-zA-Z0-9_-]*$")
    description: Optional[str] = None
    base_url: str = Field(..., min_length=1)
    auth: ConnectorAuth = Field(default_factory=ConnectorAuth)
    headers: dict[str, str] = Field(default_factory=dict)
    retry: ConnectorRetry = Field(default_factory=ConnectorRetry)
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    operations: list[OperationConfig] = Field(default_factory=list)


class ConnectorUpdate(BaseModel):
    namespace: Optional[str] = Field(None, min_length=1, max_length=100, pattern=r"^[a-zA-Z_][a-zA-Z0-9_-]*$")
    name: Optional[str] = Field(None, min_length=1, max_length=100, pattern=r"^[a-zA-Z_][a-zA-Z0-9_-]*$")
    description: Optional[str] = None
    base_url: Optional[str] = None
    auth: Optional[ConnectorAuth] = None
    headers: Optional[dict[str, str]] = None
    retry: Optional[ConnectorRetry] = None
    timeout_seconds: Optional[int] = Field(None, ge=1, le=300)
    operations: Optional[list[OperationConfig]] = None
    is_active: Optional[bool] = None


class ConnectorResponse(BaseModel):
    id: uuid.UUID
    namespace: str
    name: str
    description: Optional[str]
    base_url: str
    auth: dict[str, Any]
    headers: dict[str, Any]
    retry: dict[str, Any]
    timeout_seconds: int
    operations: list[dict[str, Any]]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ConnectorTestRequest(BaseModel):
    parameters: dict[str, Any] = Field(default_factory=dict)


class ConnectorTestResponse(BaseModel):
    status_code: int
    headers: dict[str, str]
    body: Any
    elapsed_ms: float


class OAuthAuthorizeResponse(BaseModel):
    """Where to redirect the user's browser to begin the authorization-code flow."""
    authorize_url: str


class OAuthStatusResponse(BaseModel):
    """Whether the current user has a stored OAuth token for a connector."""
    connected: bool
    expires_at: Optional[datetime] = None
    scope: Optional[str] = None


class OpenAPIImportRequest(BaseModel):
    spec: Optional[str] = None
    spec_url: Optional[str] = None
    operations: Optional[list[str]] = None  # Filter to specific operation names
    apply: bool = False


class OpenAPIImportResponse(BaseModel):
    operations: list[OperationConfig]
    warnings: list[str] = Field(default_factory=list)
    applied: int = 0
    # Spec metadata for auto-populating connector fields
    spec_title: Optional[str] = None
    spec_description: Optional[str] = None
    spec_base_url: Optional[str] = None
    # Auth derived from the spec's securitySchemes (minus secrets); None if not mappable.
    suggested_auth: Optional[dict[str, Any]] = None
