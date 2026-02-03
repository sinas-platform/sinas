import uuid
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, created_at, updated_at, uuid_pk


class Function(Base):
    __tablename__ = "functions"
    __table_args__ = (UniqueConstraint("namespace", "name", name="uix_function_namespace_name"),)

    id: Mapped[uuid_pk]
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    namespace: Mapped[str] = mapped_column(
        String(255), nullable=False, default="default", index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    input_schema: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    output_schema: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    requirements: Mapped[list[str]] = mapped_column(JSON, default=list)
    enabled_namespaces: Mapped[list[str]] = mapped_column(
        JSON, default=list
    )  # Namespaces this function can call (empty = own namespace only)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    shared_pool: Mapped[bool] = mapped_column(
        Boolean, default=False
    )  # If True, use shared worker pool instead of isolated container
    requires_approval: Mapped[bool] = mapped_column(
        Boolean, default=False
    )  # If True, LLM must ask user before calling
    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]

    # Config tracking
    managed_by: Mapped[Optional[str]] = mapped_column(Text)
    config_name: Mapped[Optional[str]] = mapped_column(Text)
    config_checksum: Mapped[Optional[str]] = mapped_column(Text)

    # Relationships
    user: Mapped["User"] = relationship("User")
    versions: Mapped[list["FunctionVersion"]] = relationship(
        "FunctionVersion", back_populates="function", cascade="all, delete-orphan"
    )

    @classmethod
    async def get_by_name(
        cls, db: AsyncSession, namespace: str, name: str, user_id: Optional[uuid.UUID] = None
    ) -> Optional["Function"]:
        """Get function by namespace and name, optionally filtered by user_id for ownership."""
        query = select(cls).where(
            cls.namespace == namespace, cls.name == name, cls.is_active == True
        )
        if user_id is not None:
            query = query.where(cls.user_id == user_id)
        result = await db.execute(query)
        return result.scalar_one_or_none()


class FunctionVersion(Base):
    __tablename__ = "function_versions"

    id: Mapped[uuid_pk]
    function_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("functions.id"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    input_schema: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    output_schema: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[created_at]
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)

    # Relationships
    function: Mapped["Function"] = relationship("Function", back_populates="versions")
    created_by_user: Mapped["User"] = relationship("User", foreign_keys=[created_by])
