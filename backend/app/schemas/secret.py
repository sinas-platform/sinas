"""Secret schemas."""
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class SecretCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    value: str = Field(..., min_length=1)
    description: Optional[str] = None


class SecretUpdate(BaseModel):
    value: Optional[str] = None
    description: Optional[str] = None


class SecretResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
