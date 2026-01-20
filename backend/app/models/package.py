from sqlalchemy import String, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import Optional
from datetime import datetime
import uuid

from .base import Base, uuid_pk


class InstalledPackage(Base):
    __tablename__ = "installed_packages"
    __table_args__ = (
        UniqueConstraint('user_id', 'package_name', name='uix_package_user_name'),
    )

    id: Mapped[uuid_pk]
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    package_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    version: Mapped[Optional[str]] = mapped_column(String(50))
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    installed_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))

    # Relationships
    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])
    installed_by_user: Mapped[Optional["User"]] = relationship("User", foreign_keys=[installed_by])