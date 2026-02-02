"""Skill model."""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.sql import func

from app.models.base import Base


class Skill(Base):
    """Skill instructions that agents can retrieve as needed."""

    __tablename__ = "skills"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False, index=True)

    description = Column(Text, nullable=False)  # What this skill helps with (shown to LLM)
    content = Column(Text, nullable=False)  # Markdown instructions (retrieved on demand)

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    is_active = Column(Boolean, default=True)

    # Config management fields
    managed_by = Column(String, nullable=True)  # "config" if managed by declarative config
    config_name = Column(String, nullable=True)  # Name of config that created this skill
    config_checksum = Column(String, nullable=True)  # Hash for change detection

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint('namespace', 'name', name='uq_skill_namespace_name'),
    )

    @classmethod
    async def get_by_name(
        cls,
        db: AsyncSession,
        namespace: str,
        name: str
    ) -> Optional["Skill"]:
        """Get skill by namespace and name."""
        result = await db.execute(
            select(cls).where(
                cls.namespace == namespace,
                cls.name == name
            )
        )
        return result.scalar_one_or_none()
