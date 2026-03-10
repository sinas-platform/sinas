"""Store schemas."""
import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class EnabledStoreConfig(BaseModel):
    """Configuration for an enabled store on an agent or component."""

    store: str = Field(..., description="Store identifier in format 'namespace/name'")
    access: str = Field(
        default="readonly", description="Access mode: 'readonly' or 'readwrite'", pattern=r"^(readonly|readwrite)$"
    )


class StoreCreate(BaseModel):
    namespace: str = Field(
        default="default", min_length=1, max_length=100, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$"
    )
    name: str = Field(..., min_length=1, max_length=100, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$")
    description: Optional[str] = None
    schema: Optional[dict[str, Any]] = Field(default=None, alias="schema")
    strict: bool = False
    default_visibility: str = Field(default="private", pattern=r"^(private|shared)$")
    encrypted: bool = False


class StoreUpdate(BaseModel):
    namespace: Optional[str] = Field(
        None, min_length=1, max_length=100, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$"
    )
    name: Optional[str] = Field(
        None, min_length=1, max_length=100, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$"
    )
    description: Optional[str] = None
    schema: Optional[dict[str, Any]] = None
    strict: Optional[bool] = None
    default_visibility: Optional[str] = Field(None, pattern=r"^(private|shared)$")
    encrypted: Optional[bool] = None


class StoreResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    namespace: str
    name: str
    description: Optional[str]
    schema: dict[str, Any] = {}
    strict: bool
    default_visibility: str
    encrypted: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
