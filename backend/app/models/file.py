"""File storage models."""
import uuid as uuid_lib
from typing import Any, Optional

from sqlalchemy import JSON, BigInteger, ForeignKey, Index, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, created_at, updated_at, uuid_pk
from .mixins import PermissionMixin


class Collection(Base, PermissionMixin):
    """
    File collection configuration.

    Collections are like S3 buckets - they store files and define validation/processing rules.
    Permissions: sinas.collections/{namespace}/{name}.{action}:scope
    """

    __tablename__ = "collections"

    id: Mapped[uuid_pk]
    namespace: Mapped[str] = mapped_column(String(100), nullable=False, index=True, default="default")
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    user_id: Mapped[uuid_lib.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Metadata schema for files in this collection (JSON Schema format)
    metadata_schema: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    # Function triggers (format: "namespace/name")
    content_filter_function: Mapped[Optional[str]] = mapped_column(String(255))
    post_upload_function: Mapped[Optional[str]] = mapped_column(String(255))

    # Limits
    max_file_size_mb: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    max_total_size_gb: Mapped[int] = mapped_column(Integer, default=10, nullable=False)

    # Access control
    allow_shared_files: Mapped[bool] = mapped_column(default=True, nullable=False)
    allow_private_files: Mapped[bool] = mapped_column(default=True, nullable=False)

    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="collections")
    files: Mapped[list["File"]] = relationship("File", back_populates="collection", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("namespace", "name", name="uq_collection_namespace_name"),
    )

    @classmethod
    async def get_by_name(cls, db: AsyncSession, namespace: str, name: str) -> Optional["Collection"]:
        """Get collection by namespace and name."""
        result = await db.execute(select(cls).where(cls.namespace == namespace, cls.name == name))
        return result.scalar_one_or_none()


class File(Base):
    """File metadata and current state."""

    __tablename__ = "files"

    id: Mapped[uuid_pk]
    collection_id: Mapped[uuid_lib.UUID] = mapped_column(
        ForeignKey("collections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    user_id: Mapped[uuid_lib.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # File properties
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # File metadata validated against collection.metadata_schema
    file_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    # Visibility control
    visibility: Mapped[str] = mapped_column(
        String(20), nullable=False, default="private", index=True
    )
    # Options: "private" (owner only), "shared" (accessible by users with collection :all permissions)

    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="files")
    collection: Mapped["Collection"] = relationship("Collection", back_populates="files")
    versions: Mapped[list["FileVersion"]] = relationship("FileVersion", back_populates="file", cascade="all, delete-orphan", order_by="FileVersion.version_number.desc()")

    __table_args__ = (
        # Performance indexes
        Index("ix_files_collection_visibility", "collection_id", "visibility"),
        Index("ix_files_user_collection", "user_id", "collection_id"),
    )


class FileVersion(Base):
    """Version history for files."""

    __tablename__ = "file_versions"

    id: Mapped[uuid_pk]
    file_id: Mapped[uuid_lib.UUID] = mapped_column(
        ForeignKey("files.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)

    # Storage location (relative path from storage root)
    # Format: "{namespace}/{file_id}/v{version_number}"
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)

    # File properties
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    hash_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # Audit
    uploaded_by: Mapped[uuid_lib.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[created_at]

    # Relationships
    file: Mapped["File"] = relationship("File", back_populates="versions")
    uploader: Mapped[Optional["User"]] = relationship("User", foreign_keys=[uploaded_by])

    __table_args__ = (
        # Unique version per file
        UniqueConstraint("file_id", "version_number", name="uq_file_version"),
        Index("ix_file_versions_hash", "hash_sha256"),
    )


class ContentFilterEvaluation(Base):
    """Content filter function execution results."""

    __tablename__ = "content_filter_evaluations"

    id: Mapped[uuid_pk]
    file_id: Mapped[uuid_lib.UUID] = mapped_column(
        ForeignKey("files.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)

    # Function that was executed
    function_namespace: Mapped[str] = mapped_column(String(100), nullable=False)
    function_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Execution result
    # Format: {"approved": bool, "reason": str, "modified_content": str|None, "metadata": dict|None}
    result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    evaluated_at: Mapped[created_at]

    # Relationships
    file: Mapped["File"] = relationship("File")

    __table_args__ = (
        Index("ix_content_filter_file_version", "file_id", "version_number"),
    )
