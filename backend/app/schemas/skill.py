"""Skill schemas."""
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
import uuid


class SkillCreate(BaseModel):
    namespace: str = Field(default="default", min_length=1, max_length=255, pattern=r'^[a-zA-Z][a-zA-Z0-9_-]*$')
    name: str = Field(..., min_length=1, max_length=255, pattern=r'^[a-zA-Z][a-zA-Z0-9_-]*$')
    description: str = Field(..., min_length=1, description="What this skill helps with (shown to LLM as tool description)")
    content: str = Field(..., min_length=1, description="Markdown instructions (retrieved when LLM calls the skill)")


class SkillUpdate(BaseModel):
    namespace: Optional[str] = Field(None, min_length=1, max_length=255, pattern=r'^[a-zA-Z][a-zA-Z0-9_-]*$')
    name: Optional[str] = Field(None, min_length=1, max_length=255, pattern=r'^[a-zA-Z][a-zA-Z0-9_-]*$')
    description: Optional[str] = Field(None, min_length=1)
    content: Optional[str] = Field(None, min_length=1)
    is_active: Optional[bool] = None


class SkillResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    namespace: str
    name: str
    description: str
    content: str
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True
