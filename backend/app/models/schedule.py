import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, created_at, uuid_pk


class ScheduledJob(Base):
    __tablename__ = "scheduled_jobs"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uix_schedule_user_name"),)

    id: Mapped[uuid_pk]
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    schedule_type: Mapped[str] = mapped_column(String(20), nullable=False, default="function")
    target_namespace: Mapped[str] = mapped_column(String(255), nullable=False, default="default")
    target_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    cron_expression: Mapped[str] = mapped_column(String(255), nullable=False)
    timezone: Mapped[str] = mapped_column(String(50), default="UTC", nullable=False)
    input_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    content: Mapped[Optional[str]] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    next_run: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[created_at]

    # Relationships
    user: Mapped["User"] = relationship("User")

    @classmethod
    async def get_by_name(
        cls, db: AsyncSession, name: str, user_id: Optional[uuid.UUID] = None
    ) -> Optional["ScheduledJob"]:
        """Get schedule by name, optionally filtered by user_id for ownership."""
        query = select(cls).where(cls.name == name)
        if user_id is not None:
            query = query.where(cls.user_id == user_id)
        result = await db.execute(query)
        return result.scalar_one_or_none()
