"""App registration model."""
import uuid
from typing import Optional

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.sql import func

from app.models.base import Base
from app.models.mixins import PermissionMixin


class App(Base, PermissionMixin):
    """Registered application that declares dependencies on SINAS resources and permissions."""

    __tablename__ = "apps"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False, index=True)

    description = Column(Text, nullable=True)

    # Resource dependencies: [{"type": "agent", "namespace": "default", "name": "assistant"}, ...]
    required_resources = Column(JSON, nullable=False, default=list)

    # Permission declarations
    required_permissions = Column(JSON, nullable=False, default=list)  # List of permission strings
    optional_permissions = Column(JSON, nullable=False, default=list)  # List of permission strings

    # Namespaces exposed to end users per resource type (soft filtering)
    # {"agents": ["default", "support"], "functions": ["default"], ...}
    exposed_namespaces = Column(JSON, nullable=False, default=dict)

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    is_active = Column(Boolean, default=True)

    # Config management fields
    managed_by = Column(String, nullable=True)
    config_name = Column(String, nullable=True)
    config_checksum = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    __table_args__ = (UniqueConstraint("namespace", "name", name="uq_app_namespace_name"),)

    @classmethod
    async def get_by_name(cls, db: AsyncSession, namespace: str, name: str) -> Optional["App"]:
        """Get app by namespace and name."""
        result = await db.execute(select(cls).where(cls.namespace == namespace, cls.name == name))
        return result.scalar_one_or_none()
