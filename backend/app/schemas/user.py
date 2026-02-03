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


class UserWithGroupsResponse(UserResponse):
    groups: list[str]  # List of group names


class UserUpdate(BaseModel):
    # No fields to update for now - placeholder for future fields
    pass
