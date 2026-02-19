"""File storage schemas."""
import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class CollectionCreate(BaseModel):
    """Schema for creating a collection."""

    namespace: str = Field(default="default", min_length=1, max_length=100, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$")
    name: str = Field(..., min_length=1, max_length=100, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$")
    metadata_schema: dict[str, Any] = Field(default_factory=dict)
    content_filter_function: Optional[str] = Field(None, pattern=r"^[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+$")
    post_upload_function: Optional[str] = Field(None, pattern=r"^[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+$")
    max_file_size_mb: int = Field(default=100, ge=1, le=1000)
    max_total_size_gb: int = Field(default=10, ge=1, le=1000)
    allow_shared_files: bool = True
    allow_private_files: bool = True


class CollectionUpdate(BaseModel):
    """Schema for updating a collection."""

    metadata_schema: Optional[dict[str, Any]] = None
    content_filter_function: Optional[str] = Field(None, pattern=r"^[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+$")
    post_upload_function: Optional[str] = Field(None, pattern=r"^[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+$")
    max_file_size_mb: Optional[int] = Field(None, ge=1, le=1000)
    max_total_size_gb: Optional[int] = Field(None, ge=1, le=1000)
    allow_shared_files: Optional[bool] = None
    allow_private_files: Optional[bool] = None


class CollectionResponse(BaseModel):
    """Schema for collection response."""

    id: uuid.UUID
    namespace: str
    name: str
    user_id: uuid.UUID
    metadata_schema: dict[str, Any]
    content_filter_function: Optional[str]
    post_upload_function: Optional[str]
    max_file_size_mb: int
    max_total_size_gb: int
    allow_shared_files: bool
    allow_private_files: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class FileUpload(BaseModel):
    """Schema for file upload."""

    name: str = Field(..., min_length=1, max_length=255, pattern=r"^[^/]+$")  # No slashes
    content_base64: str = Field(..., description="Base64-encoded file content")
    content_type: str = Field(..., min_length=1, max_length=255)
    visibility: str = Field(default="private", pattern=r"^(private|shared)$")
    file_metadata: dict[str, Any] = Field(default_factory=dict)


class FileResponse(BaseModel):
    """Schema for file response."""

    id: uuid.UUID
    namespace: str
    name: str
    user_id: uuid.UUID
    content_type: str
    current_version: int
    file_metadata: dict[str, Any]
    visibility: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class FileVersionResponse(BaseModel):
    """Schema for file version response."""

    id: uuid.UUID
    file_id: uuid.UUID
    version_number: int
    size_bytes: int
    hash_sha256: str
    uploaded_by: Optional[uuid.UUID]
    created_at: datetime

    class Config:
        from_attributes = True


class FileWithVersions(FileResponse):
    """Schema for file with version history."""

    versions: list[FileVersionResponse]


class FileDownloadResponse(BaseModel):
    """Schema for file download response."""

    content_base64: str
    content_type: str
    file_metadata: dict[str, Any]
    version: int


class FileSearchRequest(BaseModel):
    """Schema for file search request."""

    query: Optional[str] = Field(None, description="Regex pattern to search for in text files")
    metadata_filter: dict[str, Any] = Field(default_factory=dict, description="Metadata key-value filters")
    limit: int = Field(default=100, ge=1, le=1000)


class FileSearchMatch(BaseModel):
    """Schema for a single search match."""

    line: int
    text: str
    context: list[str]  # Lines before and after


class FileSearchResult(BaseModel):
    """Schema for file search result."""

    file_id: uuid.UUID
    filename: str
    version: int
    matches: list[FileSearchMatch]


class FileMetadataUpdate(BaseModel):
    """Schema for updating file metadata."""

    file_metadata: dict[str, Any]


class ContentFilterResult(BaseModel):
    """Schema for content filter function result."""

    approved: bool
    reason: Optional[str] = None
    modified_content: Optional[str] = None  # Base64
    metadata: Optional[dict[str, Any]] = None
