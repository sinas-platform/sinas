"""Package model for installable integration packages."""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, uuid_pk


class Package(Base):
    """
    Installable integration package.

    Packages bundle agents, functions, skills, apps, components, queries,
    collections, and webhooks into a single distributable YAML file.
    Resources created by a package have managed_by = "pkg:<name>".
    """

    __tablename__ = "packages"

    id: Mapped[uuid_pk]
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    author: Mapped[Optional[str]] = mapped_column(String(255))
    source_url: Mapped[Optional[str]] = mapped_column(String(1024))
    source_yaml: Mapped[str] = mapped_column(Text, nullable=False)
    installed_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    installed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), onupdate=func.now()
    )

    # Relationship
    installer: Mapped[Optional["User"]] = relationship("User", foreign_keys=[installed_by])
