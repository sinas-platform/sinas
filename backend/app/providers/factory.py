"""Factory for creating LLM provider instances."""
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import EncryptionService

from .anthropic_provider import AnthropicProvider
from .azure_openai_provider import AzureOpenAIProvider
from .gemini_provider import GeminiProvider
from .base import BaseLLMProvider
from .mistral_provider import MistralProvider
from .ollama_provider import OllamaProvider
from .openai_provider import OpenAIProvider
from .tracking import UsageTrackingProvider

# Provider settings an AGENT may override (Agent.provider_overrides).
# key -> (expected type, provider attribute). Strictly behavior settings:
# connection/credential settings (api_key, base_url, org ids) must never be
# agent-overridable — an agent author could redirect the provider's key.
# Keys whose attribute a provider doesn't have are ignored for that provider
# (e.g. prompt_caching on OpenAI, where caching is automatic).
AGENT_OVERRIDABLE: dict[str, tuple[type, str]] = {
    "prompt_caching": (bool, "enable_prompt_caching"),
}


def validate_provider_overrides(overrides: Any) -> list[str]:
    """Validate an Agent.provider_overrides value. Returns error strings."""
    if overrides is None:
        return []
    if not isinstance(overrides, dict):
        return ["provider_overrides must be an object"]
    errors = []
    for key, value in overrides.items():
        spec = AGENT_OVERRIDABLE.get(key)
        if spec is None:
            errors.append(
                f"Unknown provider override '{key}' — allowed: "
                f"{', '.join(sorted(AGENT_OVERRIDABLE))}"
            )
        elif not isinstance(value, spec[0]):
            errors.append(
                f"Provider override '{key}' must be {spec[0].__name__}, "
                f"got {type(value).__name__}"
            )
    return errors


def _apply_overrides(provider: BaseLLMProvider, overrides: Optional[dict[str, Any]]) -> None:
    if not overrides:
        return
    for key, value in overrides.items():
        spec = AGENT_OVERRIDABLE.get(key)
        if spec and isinstance(value, spec[0]) and hasattr(provider, spec[1]):
            setattr(provider, spec[1], value)


async def create_provider(
    provider_name: Optional[str] = None,
    model: Optional[str] = None,
    db: Optional[AsyncSession] = None,
    usage_context: Optional[dict[str, Any]] = None,
    overrides: Optional[dict[str, Any]] = None,
) -> BaseLLMProvider:
    """
    Create an LLM provider instance from database configuration.

    Args:
        provider_name: Name of provider (openai, ollama). If None, uses default provider from DB
        model: Model name (used to auto-detect provider if provider_name not given)
        db: Database session (optional - if not provided, falls back to empty config)
        usage_context: Attribution for token-usage records (user_id, chat_id,
            message_id, agent, source). Calls are recorded even without it,
            just unattributed.

    Returns:
        BaseLLMProvider instance, wrapped in UsageTrackingProvider so every
        complete()/stream() call is recorded in the llm_usage table

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
        provider = OpenAIProvider(api_key=api_key, base_url=base_url)
    elif provider_type in ("azure", "azure_openai"):
        config = provider_config.config or {}
        provider = AzureOpenAIProvider(
            api_key=api_key,
            azure_endpoint=base_url,
            api_version=config.get("api_version"),
            azure_deployment=config.get("azure_deployment"),
            max_tokens_param=config.get("max_tokens_param"),
            drop_params=config.get("drop_params"),
            extra_params=config.get("extra_params"),
        )
    elif provider_type == "gemini":
        provider = GeminiProvider(api_key=api_key, base_url=base_url)
    elif provider_type == "mistral":
        provider = MistralProvider(api_key=api_key, base_url=base_url)
    elif provider_type == "anthropic":
        config = provider_config.config or {}
        provider = AnthropicProvider(
            api_key=api_key,
            base_url=base_url,
            enable_prompt_caching=config.get("prompt_caching", True),
        )
    elif provider_type == "ollama":
        provider = OllamaProvider(base_url=base_url or "http://localhost:11434")
    else:
        raise ValueError(f"Unknown provider type: {provider_type}")

    # Per-agent behavior overrides (Agent.provider_overrides), applied after
    # construction so they win over the provider-level config.
    _apply_overrides(provider, overrides)

    return UsageTrackingProvider(
        inner=provider,
        provider_name=provider_config.name,
        provider_type=provider_type,
        context=usage_context,
    )
