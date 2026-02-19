"""Factory for creating LLM provider instances."""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import EncryptionService

from .anthropic_provider import AnthropicProvider
from .base import BaseLLMProvider
from .mistral_provider import MistralProvider
from .ollama_provider import OllamaProvider
from .openai_provider import OpenAIProvider


async def create_provider(
    provider_name: Optional[str] = None,
    model: Optional[str] = None,
    db: Optional[AsyncSession] = None,
) -> BaseLLMProvider:
    """
    Create an LLM provider instance from database configuration.

    Args:
        provider_name: Name of provider (openai, ollama). If None, uses default provider from DB
        model: Model name (used to auto-detect provider if provider_name not given)
        db: Database session (optional - if not provided, falls back to empty config)

    Returns:
        BaseLLMProvider instance

    Raises:
        ValueError: If provider is unknown or not found
    """
    from app.models import LLMProvider

    if not db:
        raise ValueError("Database session required to load LLM provider configuration")

    # Find provider in database
    if provider_name:
        # Find by name
        result = await db.execute(
            select(LLMProvider).where(
                LLMProvider.name == provider_name, LLMProvider.is_active == True
            )
        )
    else:
        # Use default provider (no auto-detection)
        result = await db.execute(
            select(LLMProvider).where(
                LLMProvider.is_default == True, LLMProvider.is_active == True
            )
        )

    provider_config = result.scalar_one_or_none()
    if not provider_config:
        raise ValueError(f"No active LLM provider found for: {provider_name or 'default'}")

    # Decrypt API key if present
    api_key = None
    if provider_config.api_key:
        encryption_service = EncryptionService()
        api_key = encryption_service.decrypt(provider_config.api_key)

    # Create provider instance based on type
    provider_type = provider_config.provider_type.lower()
    base_url = provider_config.api_endpoint or None

    if provider_type == "openai":
        return OpenAIProvider(api_key=api_key, base_url=base_url)
    elif provider_type == "mistral":
        return MistralProvider(api_key=api_key, base_url=base_url)
    elif provider_type == "anthropic":
        return AnthropicProvider(api_key=api_key, base_url=base_url)
    elif provider_type == "ollama":
        return OllamaProvider(base_url=base_url or "http://localhost:11434")
    else:
        raise ValueError(f"Unknown provider type: {provider_type}")
