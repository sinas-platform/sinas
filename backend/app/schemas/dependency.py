"""Dependency schemas (pip packages for function containers)."""
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class DependencyInstall(BaseModel):
    package_name: str = Field(..., min_length=1, max_length=255)
    version: Optional[str] = None


class DependencyResponse(BaseModel):
    id: uuid.UUID
    package_name: str
    version: Optional[str]
    installed_at: datetime
    installed_by: Optional[uuid.UUID]  # User ID who installed (admin)

    class Config:
        from_attributes = True
