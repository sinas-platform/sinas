"""Query model for SQL templates with namespace-based permissions."""
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


class Query(Base, PermissionMixin):
    """SQL query template with namespace-based RBAC and agent tool integration."""

    __tablename__ = "queries"
    __table_args__ = (UniqueConstraint("namespace", "name", name="uq_query_namespace_name"),)

    id: Mapped[uuid_pk]
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    namespace: Mapped[str] = mapped_column(String(255), nullable=False, default="default", index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text)  # Shown to LLM as tool description

    database_connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("database_connections.id"), nullable=False, index=True
    )

    operation: Mapped[str] = mapped_column(String(10), nullable=False)  # "read" or "write"
    sql: Mapped[str] = mapped_column(Text, nullable=False)  # SQL template with :param placeholders

    input_schema: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    output_schema: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    timeout_ms: Mapped[int] = mapped_column(Integer, default=5000)
    max_rows: Mapped[int] = mapped_column(Integer, default=1000)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]

    # Config tracking
    managed_by: Mapped[Optional[str]] = mapped_column(Text)
    config_name: Mapped[Optional[str]] = mapped_column(Text)
    config_checksum: Mapped[Optional[str]] = mapped_column(Text)

    @classmethod
    async def get_by_name(cls, db: AsyncSession, namespace: str, name: str) -> Optional["Query"]:
        """Get query by namespace and name."""
        result = await db.execute(
            select(cls).where(cls.namespace == namespace, cls.name == name)
        )
        return result.scalar_one_or_none()
