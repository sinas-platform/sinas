"""Authentication schemas."""
import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, EmailStr, Field

from app.schemas.user import UserIdentityInput


class LoginRequest(BaseModel):
    """
    Login request payload. Required fields depend on AUTH_MODE:
    - otp:          email
    - password:     email + password
    - password+otp: email + password (OTP verification follows separately)
    """

    email: EmailStr
    password: Optional[str] = None


class LoginResponse(BaseModel):
    """
    Login response. For modes that require OTP, returns session_id and the
    client should call /auth/verify-otp next. For password-only mode, tokens
    are returned directly (session_id is None).
    """

    message: str
    session_id: Optional[uuid.UUID] = None
    # When password-only, tokens are returned immediately
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    token_type: Optional[str] = None
    expires_in: Optional[int] = None
    user: Optional["AuthUserResponse"] = None


class OTPVerifyRequest(BaseModel):
    session_id: uuid.UUID
    otp_code: str


class OTPVerifyResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # Access token expiry in seconds
    user: "AuthUserResponse"


class InfoResponse(BaseModel):
    """Public, unauthenticated instance info for SDK/client discovery."""

    auth_mode: Literal["otp", "password", "password+otp"]
    version: str
    features: dict[str, bool] = Field(default_factory=dict)
    # What installed apps publish about themselves, keyed by manifest
    # namespace: the merged public_info of every active manifest in that
    # namespace. Absent namespace = not installed here.
    services: dict[str, dict[str, Any]] = Field(default_factory=dict)


class ChangePasswordRequest(BaseModel):
    """User changes their own password."""

    current_password: str
    new_password: str = Field(min_length=8)


class AdminCreateResetLinkResponse(BaseModel):
    """Returned to admin after generating a one-time password reset token."""

    user_id: uuid.UUID
    reset_token: str  # Plaintext, returned once — admin delivers out-of-band
    expires_at: datetime


class ResetPasswordRequest(BaseModel):
    """User redeems a reset token to set a new password."""

    reset_token: str
    new_password: str = Field(min_length=8)


class AuthUserResponse(BaseModel):
    id: uuid.UUID
    email: str
    last_login_at: Optional[datetime]
    created_at: datetime
    roles: list[str] = []
    # Org-specific profile data — lets external services using Sinas tokens
    # read the user's fields via /auth/me (Sinas's "userinfo" endpoint)
    custom_fields: Optional[dict[str, Any]] = None

    class Config:
        from_attributes = True


class APIKeyCreate(BaseModel):
    name: str
    permissions: dict[str, bool]
    expires_at: Optional[datetime] = None


class APIKeyResponse(BaseModel):
    id: uuid.UUID
    name: str
    key_prefix: str
    permissions: dict[str, bool]
    is_active: bool
    last_used_at: Optional[datetime]
    expires_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


class APIKeyCreatedResponse(APIKeyResponse):
    api_key: str  # Only returned once upon creation


class RefreshRequest(BaseModel):
    """Request to refresh access token using refresh token."""

    refresh_token: str


class RefreshResponse(BaseModel):
    """Response with new access token."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int  # Access token expiry in seconds


class LogoutRequest(BaseModel):
    """Request to logout (revoke refresh token)."""

    refresh_token: str


class CreateUserRequest(BaseModel):
    email: EmailStr
    custom_fields: Optional[dict[str, Any]] = None
    identities: list[UserIdentityInput] = Field(default_factory=list)


class TokenExchangeRequest(BaseModel):
    """
    Exchange an external identity for Sinas tokens. Called by a trusted
    partner backend (authenticated with an API key holding
    sinas.auth.exchange:all) after it has authenticated the user itself.
    """

    provider: str = Field(..., min_length=1, max_length=255)
    subject: str = Field(..., min_length=1, max_length=255)
    # Required for auto-provisioning and used as a linking fallback when the
    # identity is not yet known but a user with this email exists
    email: Optional[EmailStr] = None
    # Snapshot of provider claims; stored on the identity, refreshed each exchange
    metadata: Optional[dict[str, Any]] = None
    # Shallow-merged into the user's custom_fields on each exchange
    custom_fields: Optional[dict[str, Any]] = None
    # Create the user (with the configured default role) if unknown
    auto_provision: bool = False


class TokenExchangeResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: "AuthUserResponse"
    # True when this exchange created the user
    provisioned: bool = False


class PermissionCheckRequest(BaseModel):
    """Request to check if user has permission(s)."""

    permissions: list[str]
    logic: Literal["AND", "OR"] = "AND"


class PermissionCheckResult(BaseModel):
    """Individual permission check result."""

    permission: str
    has_permission: bool


class PermissionCheckResponse(BaseModel):
    """Response with permission check results."""

    result: bool  # Overall result based on logic (AND/OR)
    logic: str
    checks: list[PermissionCheckResult]
