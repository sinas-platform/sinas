"""Package schemas."""
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
import uuid


class PackageInstall(BaseModel):
    package_name: str = Field(..., min_length=1, max_length=255)
    version: Optional[str] = None


class PackageResponse(BaseModel):
    id: uuid.UUID
    package_name: str
    version: Optional[str]
    installed_at: datetime
    installed_by: Optional[str]

    class Config:
        from_attributes = True
