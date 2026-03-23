"""Connector model — named HTTP client configurations with typed operations."""
import uuid
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, created_at, updated_at, uuid_pk
from .mixins import PermissionMixin


class Connector(Base, PermissionMixin):
    """Named HTTP connector with operations exposed as agent tools."""

    __tablename__ = "connectors"

    id: Mapped[uuid_pk]
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    namespace: Mapped[str] = mapped_column(String(100), nullable=False, index=True, default="default")
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)

    # Auth: {"type": "bearer|basic|api_key|sinas_token|none", "secret": "SECRET_NAME", ...}
    auth: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict, server_default='{"type": "none"}')

    # Static default headers: {"X-Custom": "value"}
    headers: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")

    # Retry: {"max_attempts": 3, "backoff": "exponential|linear|none"}
    retry: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict, server_default='{"max_attempts": 1, "backoff": "none"}')

    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=30, server_default="30")

    # Operations: list of typed HTTP operations
    operations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list, server_default="[]")

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    # Config management
    managed_by: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    config_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    config_checksum: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]

    __table_args__ = (
        UniqueConstraint("namespace", "name", name="uq_connector_namespace_name"),
    )

    @classmethod
    async def get_by_name(cls, db, namespace: str, name: str) -> Optional["Connector"]:
        from sqlalchemy.future import select
        result = await db.execute(select(cls).where(cls.namespace == namespace, cls.name == name))
        return result.scalar_one_or_none()

    def get_operation(self, operation_name: str) -> Optional[dict[str, Any]]:
        """Get an operation by name."""
        for op in (self.operations or []):
            if op.get("name") == operation_name:
                return op
        return None
