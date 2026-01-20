from sqlalchemy import String, Text, Boolean, JSON, Enum, ForeignKey, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, Dict, Any
import enum
import uuid

from .base import Base, uuid_pk, created_at, updated_at


class HTTPMethod(str, enum.Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    DELETE = "DELETE"
    PATCH = "PATCH"


class Webhook(Base):
    __tablename__ = "webhooks"
    __table_args__ = (
        UniqueConstraint('path', 'http_method', name='uix_webhook_path_method'),
    )

    id: Mapped[uuid_pk]
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    group_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("groups.id"), index=True)
    path: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    function_namespace: Mapped[str] = mapped_column(String(255), nullable=False, default="default")
    function_name: Mapped[str] = mapped_column(String(255), nullable=False)
    http_method: Mapped[HTTPMethod] = mapped_column(
        Enum(HTTPMethod), default=HTTPMethod.POST, nullable=False
    )
    description: Mapped[Optional[str]] = mapped_column(Text)
    default_values: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    requires_auth: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]
    # Config tracking
    managed_by: Mapped[Optional[str]] = mapped_column(Text)
    config_name: Mapped[Optional[str]] = mapped_column(Text)
    config_checksum: Mapped[Optional[str]] = mapped_column(Text)


    # Relationships
    user: Mapped["User"] = relationship("User")

    @classmethod
    async def get_by_path(cls, db: AsyncSession, path: str, user_id: Optional[uuid.UUID] = None, http_method: Optional[HTTPMethod] = None) -> Optional["Webhook"]:
        """Get webhook by path, optionally filtered by user_id and HTTP method."""
        query = select(cls).where(cls.path == path, cls.is_active == True)
        if user_id is not None:
            query = query.where(cls.user_id == user_id)
        if http_method:
            query = query.where(cls.http_method == http_method)
        result = await db.execute(query)
        return result.scalar_one_or_none()