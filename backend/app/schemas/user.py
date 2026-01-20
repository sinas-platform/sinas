"""User schemas."""
from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime
import uuid


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    last_login_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


class UserWithGroupsResponse(UserResponse):
    groups: List[str]  # List of group names


class UserUpdate(BaseModel):
    # No fields to update for now - placeholder for future fields
    pass
