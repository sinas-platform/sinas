"""Database connection model for external database access."""
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, Integer, String, Text, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, created_at, updated_at, uuid_pk


class DatabaseConnection(Base):
    """
    Stores external database connection configurations.
    Admin-managed, similar to LLMProvider.
    """

    __tablename__ = "database_connections"

    id: Mapped[uuid_pk]
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    connection_type: Mapped[str] = mapped_column(String(50), nullable=False)  # postgresql, clickhouse, snowflake

    host: Mapped[str] = mapped_column(String(500), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    database: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    password: Mapped[Optional[str]] = mapped_column(Text)  # Encrypted via EncryptionService

    ssl_mode: Mapped[Optional[str]] = mapped_column(String(50))
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # Pool sizes, extra settings

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]

    # Config tracking
    managed_by: Mapped[Optional[str]] = mapped_column(Text)
    config_name: Mapped[Optional[str]] = mapped_column(Text)
    config_checksum: Mapped[Optional[str]] = mapped_column(Text)

    @classmethod
    async def get_by_name(cls, db: AsyncSession, name: str) -> Optional["DatabaseConnection"]:
        """Get database connection by name."""
        result = await db.execute(select(cls).where(cls.name == name, cls.is_active == True))
        return result.scalar_one_or_none()
