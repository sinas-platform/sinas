"""Package schemas."""
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class PackageInstall(BaseModel):
    package_name: str = Field(..., min_length=1, max_length=255)
    version: Optional[str] = None


class PackageResponse(BaseModel):
    id: uuid.UUID
    package_name: str
    version: Optional[str]
    installed_at: datetime
    installed_by: Optional[uuid.UUID]  # User ID who installed (admin)

    class Config:
        from_attributes = True
