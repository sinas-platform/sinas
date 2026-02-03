"""Group schemas."""
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class GroupCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    email_domain: Optional[str] = Field(None, max_length=255)
    external_group_id: Optional[str] = Field(None, max_length=255)


class GroupUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    email_domain: Optional[str] = Field(None, max_length=255)
    external_group_id: Optional[str] = Field(None, max_length=255)


class GroupResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: Optional[str]
    email_domain: Optional[str]
    external_group_id: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class GroupMemberResponse(BaseModel):
    id: uuid.UUID
    group_id: uuid.UUID
    user_id: uuid.UUID
    user_email: str
    role: Optional[str]
    active: bool
    added_at: datetime

    class Config:
        from_attributes = True


class GroupMemberAdd(BaseModel):
    user_id: uuid.UUID
    role: Optional[str] = "member"


class GroupPermissionResponse(BaseModel):
    id: uuid.UUID
    group_id: uuid.UUID
    permission_key: str
    permission_value: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class GroupPermissionUpdate(BaseModel):
    permission_key: str
    permission_value: bool
