"""State store schemas."""
import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class StateCreate(BaseModel):
    namespace: str = Field(..., min_length=1, max_length=100)
    key: str = Field(..., min_length=1, max_length=255)
    value: dict[str, Any] = Field(...)
    visibility: str = Field(default="private", pattern=r"^(private|shared)$")
    encrypted: bool = False
    description: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    relevance_score: float = Field(default=1.0, ge=0.0, le=1.0)
    expires_at: Optional[datetime] = None


class StateUpdate(BaseModel):
    value: Optional[dict[str, Any]] = None
    encrypted: Optional[bool] = None
    description: Optional[str] = None
    tags: Optional[list[str]] = None
    relevance_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    expires_at: Optional[datetime] = None
    visibility: Optional[str] = Field(None, pattern=r"^(private|shared)$")


class StateResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    namespace: str
    key: str
    value: dict[str, Any]
    visibility: str
    encrypted: bool = False
    description: Optional[str]
    tags: list[str]
    relevance_score: float
    expires_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
