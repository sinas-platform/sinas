from sqlalchemy import String, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import Optional
from datetime import datetime
import uuid

from .base import Base, uuid_pk


class InstalledPackage(Base):
    """
    Global packages approved by admins for use in functions.

    Packages are installed in containers based on function requirements.
    - Shared workers: Install all packages
    - User containers: Install only packages needed by accessible functions
    """
    __tablename__ = "installed_packages"
    __table_args__ = (
        UniqueConstraint('package_name', name='uix_package_name'),
    )

    id: Mapped[uuid_pk]
    package_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    version: Mapped[Optional[str]] = mapped_column(String(50))  # Locked version for reproducibility
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    installed_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"), nullable=True)  # Audit only

    # Relationship
    installed_by_user: Mapped[Optional["User"]] = relationship("User", foreign_keys=[installed_by])