"""Component model - serverless UI components stored in DB, compiled via esbuild."""
import uuid
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, created_at, updated_at, uuid_pk
from .mixins import PermissionMixin


class Component(Base, PermissionMixin):
    """Interactive React components compiled server-side and served as embeddable iframes."""

    __tablename__ = "components"
    __table_args__ = (UniqueConstraint("namespace", "name", name="uix_component_namespace_name"),)

    id: Mapped[uuid_pk]
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"), index=True)
    namespace: Mapped[str] = mapped_column(String(255), nullable=False, default="default", index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    title: Mapped[Optional[str]] = mapped_column(String(500))
    description: Mapped[Optional[str]] = mapped_column(Text)

    # Source and compiled output
    source_code: Mapped[str] = mapped_column(Text, nullable=False)
    compiled_bundle: Mapped[Optional[str]] = mapped_column(Text)
    source_map: Mapped[Optional[str]] = mapped_column(Text)
    compile_status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    compile_errors: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSON)

    # Input schema for component props
    input_schema: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)

    # Scoped resource access
    enabled_agents: Mapped[list[str]] = mapped_column(JSON, default=list)
    enabled_functions: Mapped[list[str]] = mapped_column(JSON, default=list)
    enabled_queries: Mapped[list[str]] = mapped_column(JSON, default=list)
    enabled_components: Mapped[list[str]] = mapped_column(JSON, default=list)
    state_namespaces_readonly: Mapped[list[str]] = mapped_column(JSON, default=list)
    state_namespaces_readwrite: Mapped[list[str]] = mapped_column(JSON, default=list)

    # Display and publishing
    css_overrides: Mapped[Optional[str]] = mapped_column(Text)
    visibility: Mapped[str] = mapped_column(String(50), nullable=False, default="private")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_published: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Lifecycle
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    managed_by: Mapped[Optional[str]] = mapped_column(Text)
    config_name: Mapped[Optional[str]] = mapped_column(Text)
    config_checksum: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]

    @classmethod
    async def get_by_name(
        cls, db: AsyncSession, namespace: str, name: str, user_id: Optional[uuid.UUID] = None
    ) -> Optional["Component"]:
        """Get component by namespace and name, optionally filtered by user_id."""
        query = select(cls).where(
            cls.namespace == namespace, cls.name == name, cls.is_active == True
        )
        if user_id is not None:
            query = query.where(cls.user_id == user_id)
        result = await db.execute(query)
        return result.scalar_one_or_none()
