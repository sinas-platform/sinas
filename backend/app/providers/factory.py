"""Factory for creating LLM provider instances."""
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import EncryptionService
from .base import BaseLLMProvider
from .openai_provider import OpenAIProvider
from .ollama_provider import OllamaProvider
from .mistral_provider import MistralProvider


async def create_provider(
    provider_name: Optional[str] = None,
    model: Optional[str] = None,
    db: Optional[AsyncSession] = None
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
                LLMProvider.name == provider_name,
                LLMProvider.is_active == True
            )
        )
    else:
        # Auto-detect from model name
        if model:
            if model.startswith("gpt-") or model.startswith("o1-"):
                provider_type = "openai"
            elif model.startswith("mistral-") or model.startswith("pixtral-") or model.startswith("codestral-"):
                provider_type = "mistral"
            elif "/" in model or model in ["llama", "codellama"]:
                provider_type = "ollama"
            else:
                provider_type = None

            if provider_type:
                result = await db.execute(
                    select(LLMProvider).where(
                        LLMProvider.provider_type == provider_type,
                        LLMProvider.is_active == True
                    ).order_by(LLMProvider.is_default.desc())
                )
            else:
                # Use default provider
                result = await db.execute(
                    select(LLMProvider).where(
                        LLMProvider.is_default == True,
                        LLMProvider.is_active == True
                    )
                )
        else:
            # Use default provider
            result = await db.execute(
                select(LLMProvider).where(
                    LLMProvider.is_default == True,
                    LLMProvider.is_active == True
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

    if provider_type == "openai":
        return OpenAIProvider(
            api_key=api_key,
            base_url=provider_config.api_endpoint
        )
    elif provider_type == "mistral":
        return MistralProvider(
            api_key=api_key,
            base_url=provider_config.api_endpoint
        )
    elif provider_type == "ollama":
        return OllamaProvider(
            base_url=provider_config.api_endpoint or "http://localhost:11434"
        )
    else:
        raise ValueError(f"Unknown provider type: {provider_type}")
