"""Secret schemas."""
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.core.secret_refs import SECRET_NAME_PATTERN


class SecretCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, pattern=SECRET_NAME_PATTERN)
    value: str = Field(..., min_length=1)
    description: Optional[str] = None
    visibility: str = Field(default="shared", pattern=r"^(shared|private)$")


class SecretUpdate(BaseModel):
    value: Optional[str] = None
    description: Optional[str] = None


class SecretResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: Optional[str]
    visibility: str
    user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
