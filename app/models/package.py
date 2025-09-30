from sqlalchemy import String, DateTime, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from typing import Optional
from datetime import datetime

from .base import Base, uuid_pk


class InstalledPackage(Base):
    __tablename__ = "installed_packages"
    __table_args__ = (
        UniqueConstraint('subtenant_id', 'package_name', name='uix_package_subtenant_name'),
    )

    id: Mapped[uuid_pk]
    subtenant_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    package_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    version: Mapped[Optional[str]] = mapped_column(String(50))
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    installed_by: Mapped[Optional[str]] = mapped_column(String(255))