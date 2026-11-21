"""Per-call LLM token usage records."""
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, DateTime, Index, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, GUID


class LLMUsage(Base):
    """One row per LLM API call (completion or stream), for usage accounting.

    Intentionally has no foreign keys: rows must survive deletion of the
    chat, message or user they were attributed to. chat_id/message_id are
    NULL for calls made outside the chat pipeline (e.g. the OpenAI-compat
    adapter's passthrough mode).
    """

    __tablename__ = "llm_usage"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID(), nullable=True)
    chat_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID(), nullable=True, index=True)
    message_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID(), nullable=True)

    # "namespace/name" of the agent handling the chat, if any
    agent: Mapped[Optional[str]] = mapped_column(String(511), nullable=True)
    # Call site: "chat" | "openai_adapter" | "openai_adapter_passthrough" | ...
    source: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Configured provider name and its type (openai, azure, anthropic, ...)
    provider_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    provider_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Prompt-cache portions of prompt_tokens (0 for providers without caching)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    streamed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Set when the call raised; token counts may then be 0/partial
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Why generation stopped, normalized (app/providers/tracking.py
    # FINISH_REASONS: stop, length, tool_calls, content_filter, error,
    # other), and as the provider said it (end_turn, max_tokens, ...). NULL
    # for calls recorded before it was, or when the provider gave none.
    finish_reason: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    finish_reason_raw: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Cost in USD, priced when the row is written and never recomputed
    # (app/services/llm_pricing.py). NULL: no price was known for the model.
    cost_usd: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 10), nullable=True)
    # Where the price came from: "provider" (the LLM provider's own pricing),
    # "deployment" (the MODEL_PRICES_* ratecard) or "backfill" (priced later
    # by POST /usage/backfill). NULL when unpriced.
    price_source: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # A provider batch-API call (usually discounted)
    batch: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    # Whom the call is for: the user's USAGE_CUSTOMER_FIELD custom field
    # (an organization id, say) when the call was made. NULL when that
    # setting is off or the user had no such field.
    customer: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # The call presented the run's own provider credential (a chat's
    # provider_credentials) instead of the provider's configured key. Never
    # the credential, nor a hash of it.
    run_credential: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("idx_llm_usage_user_created", "user_id", "created_at"),
        Index("idx_llm_usage_created", "created_at"),
        Index("idx_llm_usage_agent_created", "agent", "created_at"),
        Index("idx_llm_usage_provider_created", "provider_name", "created_at"),
        Index("idx_llm_usage_customer_created", "customer", "created_at"),
    )
