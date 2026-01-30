"""State store schemas."""
from pydantic import BaseModel, Field, validator
from typing import Dict, List, Optional, Any
from datetime import datetime
import uuid


class StateCreate(BaseModel):
    namespace: str = Field(..., min_length=1, max_length=100)
    key: str = Field(..., min_length=1, max_length=255)
    value: Dict[str, Any] = Field(...)
    visibility: str = Field(default="private", pattern=r'^(private|public)$')
    description: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    relevance_score: float = Field(default=1.0, ge=0.0, le=1.0)
    expires_at: Optional[datetime] = None


class StateUpdate(BaseModel):
    value: Optional[Dict[str, Any]] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    relevance_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    expires_at: Optional[datetime] = None
    visibility: Optional[str] = Field(None, pattern=r'^(private|public)$')


class StateResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    namespace: str
    key: str
    value: Dict[str, Any]
    visibility: str
    description: Optional[str]
    tags: List[str]
    relevance_score: float
    expires_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
