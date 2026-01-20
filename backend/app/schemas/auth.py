"""Authentication schemas."""
from pydantic import BaseModel, EmailStr
from typing import Optional, Dict, List, Literal
from datetime import datetime
import uuid


class LoginRequest(BaseModel):
    email: EmailStr


class LoginResponse(BaseModel):
    message: str
    session_id: uuid.UUID


class OTPVerifyRequest(BaseModel):
    session_id: uuid.UUID
    otp_code: str


class OTPVerifyResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # Access token expiry in seconds
    user: "UserResponse"


class ExternalAuthRequest(BaseModel):
    """Request to exchange external OIDC token for SINAS JWT."""
    token: str


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    last_login_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


class APIKeyCreate(BaseModel):
    name: str
    permissions: Dict[str, bool]
    expires_at: Optional[datetime] = None


class APIKeyResponse(BaseModel):
    id: uuid.UUID
    name: str
    key_prefix: str
    permissions: Dict[str, bool]
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


class PermissionCheckRequest(BaseModel):
    """Request to check if user has permission(s)."""
    permissions: List[str]
    logic: Literal["AND", "OR"] = "AND"


class PermissionCheckResult(BaseModel):
    """Individual permission check result."""
    permission: str
    has_permission: bool


class PermissionCheckResponse(BaseModel):
    """Response with permission check results."""
    result: bool  # Overall result based on logic (AND/OR)
    logic: str
    checks: List[PermissionCheckResult]
