"""API Key schemas."""
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class APIKeyCreate(BaseModel):
    """Request to create a new API key."""

    name: str = Field(
        ..., min_length=1, max_length=255, description="Friendly name for the API key"
    )
    permissions: dict[str, bool] = Field(
        default_factory=dict,
        description="Permission overrides (empty = inherit from user's groups)",
    )
    expires_at: Optional[datetime] = Field(None, description="Optional expiration date")


class APIKeyResponse(BaseModel):
    """API key information (without the actual key)."""

    id: uuid.UUID
    user_id: uuid.UUID
    user_email: Optional[str] = None  # Owner's email (only shown to admins with :all scope)
    name: str
    key_prefix: str  # e.g., "sk-abc..."
    permissions: dict[str, bool]
    is_active: bool
    last_used_at: Optional[datetime]
    expires_at: Optional[datetime]
    created_at: datetime
    revoked_at: Optional[datetime]

    class Config:
        from_attributes = True


class APIKeyCreated(BaseModel):
    """Response when API key is created (includes the plain key - shown only once)."""

    id: uuid.UUID
    name: str
    key: str  # Plain API key - only returned once on creation
    key_prefix: str
    permissions: dict[str, bool]
    expires_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True
