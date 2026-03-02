"""Database trigger model for CDC (Change Data Capture) via polling."""
import uuid
from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, created_at, updated_at, uuid_pk


class DatabaseTrigger(Base):
    """
    Stores CDC trigger configurations that poll external databases for changes
    and enqueue function executions when new/updated rows are detected.
    """

    __tablename__ = "database_triggers"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uix_db_trigger_user_name"),)

    id: Mapped[uuid_pk]
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    database_connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("database_connections.id"), nullable=False, index=True
    )
    schema_name: Mapped[str] = mapped_column(String(255), nullable=False, default="public")
    table_name: Mapped[str] = mapped_column(String(255), nullable=False)
    operations: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    function_namespace: Mapped[str] = mapped_column(String(255), nullable=False, default="default")
    function_name: Mapped[str] = mapped_column(String(255), nullable=False)
    poll_column: Mapped[str] = mapped_column(String(255), nullable=False)
    poll_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    batch_size: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_poll_value: Mapped[Optional[str]] = mapped_column(Text)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]

    # Config tracking
    managed_by: Mapped[Optional[str]] = mapped_column(Text)
    config_name: Mapped[Optional[str]] = mapped_column(Text)
    config_checksum: Mapped[Optional[str]] = mapped_column(Text)

    # Relationships
    user: Mapped["User"] = relationship("User")
    database_connection: Mapped["DatabaseConnection"] = relationship("DatabaseConnection")
