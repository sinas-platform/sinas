"""Factory for creating LLM provider instances."""
import logging
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import EncryptionService

from .anthropic_provider import AnthropicProvider
from .azure_openai_provider import AzureOpenAIProvider
from .gemini_provider import GeminiProvider
from .base import BaseLLMProvider
from .errors import PROVIDER_NOT_CONFIGURED, LLMError
from .mistral_provider import MistralProvider
from .ollama_provider import OllamaProvider
from .openai_provider import OpenAIProvider
from .tracking import UsageTrackingProvider

logger = logging.getLogger(__name__)

# Provider settings an AGENT may override (Agent.provider_overrides).
# key -> (expected type, provider attribute, allowed values or None).
# Strictly behavior settings: connection/credential settings (api_key,
# base_url, org ids) must never be agent-overridable — an agent author could
# redirect the provider's key. Keys whose attribute a provider doesn't have
# are ignored for that provider (e.g. prompt_caching on OpenAI, where caching
# is automatic; effort on every non-Anthropic provider).
#
# effort — Anthropic `output_config.effort`. On current Claude models thinking
# is ON by default even when no `thinking` parameter is sent, and effort is
# the only control over how much the model thinks and spends: `thinking:
# {type: "enabled", budget_tokens}` is rejected with a 400 on Claude Sonnet 5 /
# Opus 5 and later, and `{type: "disabled"}` is rejected on Opus 5.5 and
# Sonnet 5.5. Without this, every Anthropic agent thought at the model's
# default effort (`high` on most models) with no way to turn it down.
#
# Model support varies and is left to the API to enforce, like the rest of
# the request: Claude Haiku 4.5 and Sonnet 4.5 reject effort outright, and
# `xhigh` needs Opus 4.7 / Sonnet 5 or later. That is also why there is no
# provider-level default — a provider serving Haiku agents alongside Sonnet
# ones would break every Haiku request.
EFFORT_LEVELS = frozenset({"low", "medium", "high", "xhigh", "max"})

AGENT_OVERRIDABLE: dict[str, tuple[type, str, Optional[frozenset]]] = {
    "prompt_caching": (bool, "enable_prompt_caching", None),
    "effort": (str, "effort", EFFORT_LEVELS),
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
            continue
        expected, _, allowed = spec
        if not isinstance(value, expected):
            errors.append(
                f"Provider override '{key}' must be {expected.__name__}, "
                f"got {type(value).__name__}"
            )
        elif allowed is not None and value not in allowed:
            errors.append(
                f"Provider override '{key}' must be one of "
                f"{', '.join(sorted(allowed))}, got {value!r}"
            )
    return errors


def provider_not_configured(provider_name: Optional[str]) -> LLMError:
    """The `provider_not_configured` error for a provider that can't be used:
    the named one (missing or inactive), or no active default. Says what an
    admin can do about it, and nothing about the provider's configuration."""
    if provider_name:
        message = (
            f"LLM provider '{provider_name}' is not available: it doesn't exist or is inactive. "
            "An admin can activate it under LLM Providers, or set another provider for this agent."
        )
    else:
        message = (
            "No LLM provider is configured. An admin can add one under LLM Providers "
            "(and make it the default), or set this agent's provider."
        )
    return LLMError(PROVIDER_NOT_CONFIGURED, message, provider=provider_name)


def _override_is_valid(key: str, value: Any) -> bool:
    spec = AGENT_OVERRIDABLE.get(key)
    if spec is None:
        return False
    expected, _, allowed = spec
    return isinstance(value, expected) and (allowed is None or value in allowed)


def _apply_overrides(provider: BaseLLMProvider, overrides: Optional[dict[str, Any]]) -> None:
    if not overrides:
        return
    for key, value in overrides.items():
        # Re-checked here, not only at write time: rows written before a key
        # existed or before its allowed values changed must not reach the API.
        if _override_is_valid(key, value):
            attribute = AGENT_OVERRIDABLE[key][1]
            if hasattr(provider, attribute):
                setattr(provider, attribute, value)


def _build_provider(provider_config: Any, provider_type: str, api_key: Optional[str]) -> BaseLLMProvider:
    """The provider instance for a provider row of `provider_type`."""
    base_url = provider_config.api_endpoint or None

    if provider_type == "openai":
        return OpenAIProvider(api_key=api_key, base_url=base_url)
    elif provider_type in ("azure", "azure_openai"):
        config = provider_config.config or {}
        return AzureOpenAIProvider(
            api_key=api_key,
            azure_endpoint=base_url,
            api_version=config.get("api_version"),
            azure_deployment=config.get("azure_deployment"),
            max_tokens_param=config.get("max_tokens_param"),
            drop_params=config.get("drop_params"),
            extra_params=config.get("extra_params"),
        )
    elif provider_type == "gemini":
        return GeminiProvider(api_key=api_key, base_url=base_url)
    elif provider_type == "mistral":
        return MistralProvider(api_key=api_key, base_url=base_url)
    elif provider_type == "anthropic":
        config = provider_config.config or {}
        return AnthropicProvider(
            api_key=api_key,
            base_url=base_url,
            enable_prompt_caching=config.get("prompt_caching", True),
        )
    elif provider_type == "ollama":
        return OllamaProvider(base_url=base_url or "http://localhost:11434")
    else:
        raise LLMError(
            PROVIDER_NOT_CONFIGURED,
            f"LLM provider '{provider_config.name}' has an unsupported type '{provider_type}'. "
            "An admin can fix it under LLM Providers.",
            provider=provider_config.name,
        )


async def _attach_run_credential(
    db: AsyncSession,
    provider: BaseLLMProvider,
    provider_config: Any,
    provider_type: str,
    provider_settings: dict[str, Any],
    usage_context: Optional[dict[str, Any]],
) -> bool:
    """Give `provider` the run credential this call presents, if any. True
    when it has one; raises `provider_credential_required` when the provider
    requires one and the call has none (or only an expired one)."""
    from . import run_credentials
    from .errors import PROVIDER_CREDENTIAL_REQUIRED

    settings = run_credentials.settings_of(provider_settings, provider_type)
    if not settings.enabled:
        if settings.required:  # an invalid stored setting that asks for one
            raise LLMError(
                PROVIDER_CREDENTIAL_REQUIRED,
                f"LLM provider '{provider_config.name}' requires a run credential, but its "
                "run_credential setting is invalid. An admin can fix it under LLM Providers.",
                provider=provider_config.name,
            )
        return False

    credential: Optional[str] = None
    status = "missing"
    override = run_credentials.REQUEST_CREDENTIAL.get()
    if override is not None and override[0] == provider_config.name:
        credential, status = override[1], "active"
    else:
        chat_id = (usage_context or {}).get("chat_id")
        if chat_id:
            from app.services.provider_credentials import resolve

            credential, status = await resolve(db, chat_id, provider_config.name)

    if credential is None:
        if not settings.required:
            return False
        if status == "expired":
            detail = (
                "the run credential this chat was given for it has expired. Send a new one "
                "(provider_credentials) with the next message."
            )
        else:
            detail = (
                "this chat has no run credential for it. Send one (provider_credentials) when "
                "creating the chat or with a message."
            )
        raise LLMError(
            PROVIDER_CREDENTIAL_REQUIRED,
            f"LLM provider '{provider_config.name}' requires a run credential, and {detail}",
            provider=provider_config.name,
        )

    provider.run_credential = run_credentials.RunCredentialHeaders(
        run_credentials.credential_headers(settings, provider_type, credential), credential
    )
    return True


async def create_provider(
    provider_name: Optional[str] = None,
    model: Optional[str] = None,
    db: Optional[AsyncSession] = None,
    usage_context: Optional[dict[str, Any]] = None,
    overrides: Optional[dict[str, Any]] = None,
    config: Optional[Any] = None,
    run_credentials: bool = True,
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
        config: The provider's row, when the caller already loaded it (the
            one `provider_name`, or the default, would find). Saves a query.
        run_credentials: False for a provider that only manages work already
            sent (a provider batch's status, results, cancellation): it
            presents the configured key, whatever the provider's
            run_credential setting (app/providers/run_credentials.py).

    Returns:
        BaseLLMProvider instance, wrapped in UsageTrackingProvider so every
        complete()/stream() call is recorded in the llm_usage table

    Raises:
        LLMError: `provider_not_configured`, when the provider is not found,
            inactive, or of an unknown type (app/providers/errors.py)
    """
    from app.models import LLMProvider

    if not db:
        raise ValueError("Database session required to load LLM provider configuration")

    # Find provider in database
    if config is not None and config.is_active and (
        config.name == provider_name if provider_name else config.is_default
    ):
        result = None
    elif provider_name:
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

    provider_config = config if result is None else result.scalar_one_or_none()
    if not provider_config:
        raise provider_not_configured(provider_name)

    # Create provider instance based on type
    provider_type = provider_config.provider_type.lower()
    try:
        # Decrypt API key if present
        api_key = None
        if provider_config.api_key:
            encryption_service = EncryptionService()
            api_key = encryption_service.decrypt(provider_config.api_key)
        provider = _build_provider(provider_config, provider_type, api_key)
    except LLMError:
        raise
    except Exception as e:
        # An incomplete or invalid configuration (no API key, no endpoint,
        # ...): the details go to the log, not to whoever's turn it was.
        logger.warning(f"LLM provider '{provider_config.name}' could not be set up: {e}")
        raise LLMError(
            PROVIDER_NOT_CONFIGURED,
            f"LLM provider '{provider_config.name}' could not be set up: its configuration is "
            "incomplete or invalid (such as a missing API key or endpoint). An admin can fix it "
            "under LLM Providers.",
            provider=provider_config.name,
        ) from e

    # Provider-level only, never per agent: whether a call carries the trace
    # context and baggage to the model API (see BaseLLMProvider).
    provider_settings = provider_config.config or {}
    provider.propagate_context = bool(provider_settings.get("propagate_context"))

    # Identity headers (config.headers) for this call's user, agent, chat and
    # API key, and the customer its usage row is attributed to. Resolved here,
    # from the usage context every call site passes, rather than from the
    # request that started the turn: the same holds for a queued job, a
    # resume, a sub-agent or a scheduled run (app/providers/attribution.py).
    from .attribution import resolve_attribution
    from .errors import validate_budget_rules

    identity_headers, customer = await resolve_attribution(
        db, usage_context, provider_settings.get("headers")
    )
    provider.identity_headers = identity_headers
    try:
        provider.budget_rules = validate_budget_rules(provider_settings.get("budget_errors"))
    except ValueError as e:  # written by hand into the database
        logger.warning(f"LLM provider '{provider_config.name}': budget_errors ignored: {e}")
        provider.budget_rules = []
    if customer is not None:
        usage_context = {**(usage_context or {}), "customer": customer}

    # The run's own credential for this provider, when it has opted in
    # (config.run_credential): the request's (the OpenAI-compatible
    # adapter's header), else the chat's, else — unless required — none, and
    # the configured key serves the call (app/providers/run_credentials.py).
    if run_credentials:
        await _attach_run_credential(
            db, provider, provider_config, provider_type, provider_settings, usage_context
        )

    # Per-agent behavior overrides (Agent.provider_overrides), applied after
    # construction so they win over the provider-level config.
    _apply_overrides(provider, overrides)

    return UsageTrackingProvider(
        inner=provider,
        provider_name=provider_config.name,
        provider_type=provider_type,
        context=usage_context,
        budget_rules=provider.budget_rules,
    )
