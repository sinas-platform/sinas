"""User schemas."""
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    last_login_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


class UserWithRolesResponse(BaseModel):
    id: uuid.UUID
    email: str
    last_login_at: Optional[datetime] = None
    created_at: datetime
    roles: list[str]

    class Config:
        from_attributes = True


class UserUpdate(BaseModel):
    # No fields to update for now - placeholder for future fields
    pass
