"""ComponentShare model - shareable links for components with scoped access."""
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, created_at, uuid_pk


class ComponentShare(Base):
    """A share link that grants access to a component without requiring authentication."""

    __tablename__ = "component_shares"

    id: Mapped[uuid_pk]
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    component_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("components.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )

    # Input data baked into the share link
    input_data: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)

    # Expiry and usage limits
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    max_views: Mapped[Optional[int]] = mapped_column(Integer)
    view_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Label for management
    label: Mapped[Optional[str]] = mapped_column(String(255))

    created_at: Mapped[created_at]

    @classmethod
    async def get_by_token(cls, db: AsyncSession, token: str) -> Optional["ComponentShare"]:
        """Get share by token."""
        result = await db.execute(select(cls).where(cls.token == token))
        return result.scalar_one_or_none()
