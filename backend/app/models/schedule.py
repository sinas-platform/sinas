from sqlalchemy import String, Text, Boolean, JSON, DateTime, ForeignKey, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, Dict, Any
from datetime import datetime
import uuid

from .base import Base, uuid_pk, created_at


class ScheduledJob(Base):
    __tablename__ = "scheduled_jobs"
    __table_args__ = (
        UniqueConstraint('user_id', 'name', name='uix_schedule_user_name'),
    )

    id: Mapped[uuid_pk]
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    group_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("groups.id"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    function_namespace: Mapped[str] = mapped_column(String(255), nullable=False, default="default")
    function_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    cron_expression: Mapped[str] = mapped_column(String(255), nullable=False)
    timezone: Mapped[str] = mapped_column(String(50), default="UTC", nullable=False)
    input_data: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    next_run: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[created_at]

    # Relationships
    user: Mapped["User"] = relationship("User")

    @classmethod
    async def get_by_name(cls, db: AsyncSession, name: str, user_id: Optional[uuid.UUID] = None) -> Optional["ScheduledJob"]:
        """Get schedule by name, optionally filtered by user_id for ownership."""
        query = select(cls).where(cls.name == name, cls.is_active == True)
        if user_id is not None:
            query = query.where(cls.user_id == user_id)
        result = await db.execute(query)
        return result.scalar_one_or_none()