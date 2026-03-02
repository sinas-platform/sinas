"""Package schemas for installable integration packages."""
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class PackageInstallRequest(BaseModel):
    """Request to install a package from YAML content."""
    source: str = Field(..., description="YAML content of the SinasPackage")


class PackagePreviewRequest(BaseModel):
    """Request to preview a package install (dry run)."""
    source: str = Field(..., description="YAML content of the SinasPackage")


class PackageResourceRef(BaseModel):
    """Reference to a resource for package creation."""
    type: str = Field(..., description="Resource type: agent, function, skill, app, component, query, collection, template, webhook, schedule")
    namespace: str = "default"
    name: str


class PackageCreateRequest(BaseModel):
    """Request to create a package YAML from selected resources."""
    name: str
    version: str = "1.0.0"
    description: Optional[str] = None
    author: Optional[str] = None
    url: Optional[str] = None
    resources: list[PackageResourceRef]


class PackageResponse(BaseModel):
    """Full package details."""
    id: uuid.UUID
    name: str
    version: str
    description: Optional[str]
    author: Optional[str]
    source_url: Optional[str]
    installed_by: uuid.UUID
    installed_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


class PackageListResponse(BaseModel):
    """Package summary for list view."""
    id: uuid.UUID
    name: str
    version: str
    description: Optional[str]
    author: Optional[str]
    installed_at: datetime

    class Config:
        from_attributes = True
