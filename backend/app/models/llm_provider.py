from sqlalchemy import String, Text, Boolean, JSON, select
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, Dict, Any
import uuid

from .base import Base, uuid_pk, created_at, updated_at


class LLMProvider(Base):
    """
    Stores LLM provider configurations (API keys, endpoints, etc.)
    Replaces environment variables for LLM configuration.
    """
    __tablename__ = "llm_providers"

    id: Mapped[uuid_pk]
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    # Provider type: "openai", "anthropic", "ollama", "azure", etc.
    provider_type: Mapped[str] = mapped_column(String(50), nullable=False)

    # API configuration (encrypted in database)
    api_key: Mapped[Optional[str]] = mapped_column(Text)  # Encrypted with ENCRYPTION_KEY
    api_endpoint: Mapped[Optional[str]] = mapped_column(String(500))  # For custom endpoints
    default_model: Mapped[Optional[str]] = mapped_column(String(100))  # Default model if not specified

    # Additional configuration (rate limits, organization_id, etc.)
    config: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    # Example: {"max_tokens": 128000, "organization_id": "org-..."}

    # Default provider for new assistants
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]

    # Config tracking
    managed_by: Mapped[Optional[str]] = mapped_column(Text)
    config_name: Mapped[Optional[str]] = mapped_column(Text)
    config_checksum: Mapped[Optional[str]] = mapped_column(Text)

    @classmethod
    async def get_by_name(cls, db: AsyncSession, name: str) -> Optional["LLMProvider"]:
        """Get LLM provider by name."""
        result = await db.execute(
            select(cls).where(cls.name == name, cls.is_active == True)
        )
        return result.scalar_one_or_none()
