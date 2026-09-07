"""Package schemas for installable integration packages."""
import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


INSTANCE_NAME_PATTERN = r"^[a-z0-9][a-z0-9-]{0,62}$"


class PackageInstallRequest(BaseModel):
    """Request to install a package from YAML content."""
    source: str = Field(..., description="YAML content of the SinasPackage")
    variables: Optional[dict[str, Any]] = Field(None, description="Install-time variable values")
    instance: Optional[str] = Field(
        None,
        pattern=INSTANCE_NAME_PATTERN,
        description=(
            "Install name for a package with package.multiInstance: true, so the same "
            "package can be installed more than once (e.g. records-work, records-personal). "
            "Substituted for ${{ install.name }} in the YAML. Defaults to the package name."
        ),
    )
    allowBroadRolePermissions: bool = Field(
        False,
        description=(
            "Accept package roles whose granted permissions reach outside the "
            "package's own namespaces (listed by preview). Off by default."
        ),
    )


class PackagePreviewRequest(BaseModel):
    """Request to preview a package install (dry run)."""
    source: str = Field(..., description="YAML content of the SinasPackage")
    variables: Optional[dict[str, Any]] = Field(None, description="Variable values for substitution")
    instance: Optional[str] = Field(
        None, pattern=INSTANCE_NAME_PATTERN,
        description="Install name to preview under (multi-instance packages)",
    )


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
    package_name: Optional[str] = None   # declared name; differs from name for a named instance
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
    package_name: Optional[str] = None
    version: str
    description: Optional[str]
    author: Optional[str]
    installed_at: datetime

    class Config:
        from_attributes = True


class PackageInstallResponse(BaseModel):
    """Response from package install."""
    package: dict[str, Any]
    apply: dict[str, Any]
