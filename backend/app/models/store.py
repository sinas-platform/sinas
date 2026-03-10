"""Store model — definition layer for state namespaces."""
import uuid
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, created_at, updated_at, uuid_pk
from .mixins import PermissionMixin


class Store(Base, PermissionMixin):
    """Definition for a state namespace (schema, strict mode, encryption, etc.)."""

    __tablename__ = "stores"

    id: Mapped[uuid_pk]
    namespace: Mapped[str] = mapped_column(String(100), nullable=False, index=True, default="default")
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    user_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False, index=True
    )

    # Schema enforcement
    schema: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")
    strict: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")

    # Defaults for states created in this store
    default_visibility: Mapped[str] = mapped_column(String(20), nullable=False, default="private", server_default="private")
    encrypted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")

    # Config management
    managed_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    config_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    config_checksum: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]

    # Relationships
    states: Mapped[list["State"]] = relationship("State", back_populates="store", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("namespace", "name", name="uq_store_namespace_name"),
    )

    @classmethod
    async def get_by_name(cls, db, namespace: str, name: str) -> Optional["Store"]:
        """Get store by namespace and name."""
        from sqlalchemy.future import select
        result = await db.execute(select(cls).where(cls.namespace == namespace, cls.name == name))
        return result.scalar_one_or_none()
