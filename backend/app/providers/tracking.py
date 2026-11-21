"""Usage-tracking wrapper around LLM providers.

Every provider returned by ``create_provider()`` is wrapped in
:class:`UsageTrackingProvider`, which records one ``llm_usage`` row per
LLM call (token counts, latency, attribution, and the cost at the prices
that apply at that moment — see app/services/llm_pricing.py). Recording is
fire-and-forget on a dedicated DB session: a failed insert never blocks or
fails the call, and it is isolated from whatever transaction state the
caller's session is in.

A request that reports the usage of the calls it caused (the synchronous
agent invoke) opens a :func:`collect_usage` scope: every call recorded in
that async context is also handed to the scope's :class:`UsageCollector`,
which waits for the rows to commit and totals them.
"""
import asyncio
import logging
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal
from typing import Any, Optional

from .base import BaseLLMProvider
from .errors import PROVIDER_ERROR, LLMError, contains_secret, error_from_exception, scrub

logger = logging.getLogger(__name__)

# The finish reasons llm_usage records (`finish_reason`), whatever the
# provider calls them; the provider's own value is kept in
# `finish_reason_raw`.
FINISH_REASONS = ("stop", "length", "tool_calls", "content_filter", "error", "other")
# The `error` of a call cancelled while it waited on the model API.
CANCELLED = "cancelled"
_FINISH_REASON_ALIASES = {
    # OpenAI, Azure, Gemini (OpenAI-compatible), Mistral, Ollama
    "stop": "stop",
    "length": "length",
    "tool_calls": "tool_calls",
    "function_call": "tool_calls",
    "content_filter": "content_filter",
    "error": "error",
    "model_length": "length",
    # Anthropic
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "model_context_window_exceeded": "length",
    "tool_use": "tool_calls",
    "refusal": "content_filter",
    # Gemini's native names, as some gateways pass them on
    "max_output_tokens": "length",
    "safety": "content_filter",
    "recitation": "content_filter",
    "blocklist": "content_filter",
    "prohibited_content": "content_filter",
    "spii": "content_filter",
    "image_safety": "content_filter",
    "malformed_function_call": "error",
}


def normalize_finish_reason(raw: Any) -> Optional[str]:
    """One of FINISH_REASONS for a provider's finish/stop reason, or None
    when it gave none. Unknown values are "other"."""
    text = raw_finish_reason(raw)
    if text is None:
        return None
    text = text.lower()
    if text.startswith("finish_reason_"):  # FINISH_REASON_STOP and the like
        text = text[len("finish_reason_"):]
    return _FINISH_REASON_ALIASES.get(text, "other")


def raw_finish_reason(raw: Any) -> Optional[str]:
    """The provider's own finish reason as text (an SDK enum's value)."""
    if raw is None:
        return None
    text = str(getattr(raw, "value", raw)).strip()
    return text[:50] or None

# Context keys recognized on llm_usage rows (all optional):
# user_id, chat_id, message_id, agent, source, and customer (the
# USAGE_CUSTOMER_FIELD custom field of the user, set by create_provider)
_CONTEXT_KEYS = ("user_id", "chat_id", "message_id", "agent", "source", "customer")


async def _insert_usage(values: dict[str, Any]) -> bool:
    """Price and save one llm_usage row. True only when the row committed."""
    try:
        from app.core.database import AsyncSessionLocal
        from app.models import LLMUsage
        from app.services.llm_pricing import price_usage

        async with AsyncSessionLocal() as session:
            await price_usage(session, values)
            session.add(LLMUsage(**values))
            await session.commit()
        return True
    except Exception as e:
        logger.warning(f"Failed to record LLM usage: {e}")
        return False


# How long UsageCollector.totals() waits for outstanding usage inserts.
_SETTLE_TIMEOUT_SECONDS = 10.0

_TOKEN_FIELDS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
)


def _saved(task: asyncio.Task) -> bool:
    """Whether an insert task finished and reported its row committed."""
    if not task.done() or task.cancelled() or task.exception() is not None:
        return False
    return task.result() is True


class UsageCollector:
    """The llm_usage rows recorded in one async context, e.g. one invoke.

    Attribution is by context, not by chat or time window: a call counts
    when it was recorded by code running in the scope's context — the
    request's own task and every task it spawned (tool rounds, parallel tool
    calls). Calls made in another process (sub-agents run by queue workers)
    or by work that detached itself (background compaction) don't count.
    """

    def __init__(self) -> None:
        self._entries: list[tuple[dict[str, Any], asyncio.Task]] = []
        self._closed = False

    def add(self, values: dict[str, Any], task: asyncio.Task) -> None:
        # A straggler that inherited the context and records after the
        # totals were taken is not part of them.
        if not self._closed:
            self._entries.append((values, task))

    async def totals(self, timeout: float = _SETTLE_TIMEOUT_SECONDS) -> dict[str, Any]:
        """Wait for the collected rows to be priced and committed, then total
        them. Field names match the /usage/summary metrics.

        `cost_usd` adds up the priced calls and is None when calls were made
        but none was priced. `cost_usd` only ever includes rows that were
        saved: a call whose row failed to commit, or hadn't committed within
        `timeout`, counts as unpriced, so the cost matches what /usage
        reports. Its tokens are still counted — the call was made and the
        provider bills it — so in that rare case the token totals exceed
        what /usage has on record; `unpriced_calls` shows it.
        """
        self._closed = True
        entries = list(self._entries)
        if entries:
            _, pending = await asyncio.wait([task for _, task in entries], timeout=timeout)
            if pending:
                logger.warning(
                    f"{len(pending)} LLM usage record(s) not committed after {timeout}s; "
                    "reporting them as unpriced"
                )
        result: dict[str, Any] = {field: 0 for field in _TOKEN_FIELDS}
        result.update(calls=len(entries), unpriced_calls=0, errors=0)
        cost: Optional[Decimal] = None
        for values, task in entries:
            for field in _TOKEN_FIELDS:
                result[field] += int(values.get(field) or 0)
            if values.get("error") is not None:
                result["errors"] += 1
            call_cost = values.get("cost_usd") if _saved(task) else None
            if call_cost is None:
                result["unpriced_calls"] += 1
            else:
                cost = (cost or Decimal(0)) + Decimal(call_cost)
        if cost is not None:
            result["cost_usd"] = float(cost)
        else:
            result["cost_usd"] = None if entries else 0.0
        return result


_collector: ContextVar[Optional[UsageCollector]] = ContextVar("llm_usage_collector", default=None)


@contextmanager
def collect_usage() -> Iterator[UsageCollector]:
    """Collect the usage of every LLM call recorded in this context, and in
    the tasks it spawns, until the block exits."""
    collector = UsageCollector()
    token = _collector.set(collector)
    try:
        yield collector
    finally:
        _collector.reset(token)


def detach_usage_collector() -> None:
    """Keep the calling task's LLM calls out of an enclosing collect_usage()
    scope. For fire-and-forget work that merely inherited the request's
    context; it only changes the calling task's own context."""
    _collector.set(None)


class UsageTrackingProvider(BaseLLMProvider):
    """Delegates to an inner provider and records token usage per call."""

    def __init__(
        self,
        inner: BaseLLMProvider,
        provider_name: Optional[str] = None,
        provider_type: Optional[str] = None,
        context: Optional[dict[str, Any]] = None,
        budget_rules: Optional[list[dict[str, Any]]] = None,
    ):
        super().__init__(api_key=inner.api_key, base_url=inner.base_url)
        self._inner = inner
        self.run_credential = getattr(inner, "run_credential", None)
        self._provider_name = provider_name
        self._provider_type = provider_type
        self._context = context or {}
        self._budget_rules = budget_rules or []

    def _classify(self, error: BaseException, model: Optional[str] = None) -> Optional[LLMError]:
        """The typed error (budget_exceeded, rate_limited, provider_timeout,
        ...) a provider error stands for, if any (app/providers/errors.py)."""
        try:
            return error_from_exception(error, self._budget_rules, self._provider_name, model)
        except Exception:  # classifying must never hide the original error
            logger.debug("Classifying an LLM error failed", exc_info=True)
            return None

    def _secrets(self) -> list[str]:
        credential = getattr(self._inner, "run_credential", None)
        return credential.secrets() if credential is not None else []

    def _failure(self, error: Exception, model: Optional[str]) -> tuple[Optional[LLMError], bool]:
        """(the typed error to raise, whether to hide the original) for a
        failed call. With a run credential, no trace of it leaves: a typed
        error's message is scrubbed of it, and an error whose own text holds
        it (a gateway echoing the key it was sent) is raised as a typed one
        without its cause."""
        typed = self._classify(error, model)
        secrets = self._secrets()
        if not secrets:
            return typed, False
        hide = contains_secret(str(error), secrets) or contains_secret(repr(error), secrets)
        if typed is not None:
            return typed.scrubbed(secrets), hide
        if hide:
            where = f"LLM provider '{self._provider_name}'" if self._provider_name else "The LLM provider"
            return LLMError(
                PROVIDER_ERROR,
                f"{where} failed: {scrub(str(error), secrets)[:500]}",
                provider=self._provider_name,
            ), True
        return None, False

    def _record(
        self,
        *,
        model: Optional[str],
        usage: Optional[dict[str, int]],
        latency_ms: int,
        streamed: bool,
        error: Optional[str] = None,
        finish_reason: Any = None,
    ) -> None:
        usage = usage or {}
        normalized = normalize_finish_reason(finish_reason)
        if error is not None and error != "stream_abandoned":
            # The call failed, even if a finish reason arrived first (OpenAI
            # sends it before the usage chunk); finish_reason_raw keeps it.
            normalized = "error"
        values = {
            "provider_name": self._provider_name,
            "provider_type": self._provider_type,
            "model": model,
            "prompt_tokens": usage.get("prompt_tokens", 0) or 0,
            "completion_tokens": usage.get("completion_tokens", 0) or 0,
            "total_tokens": usage.get("total_tokens", 0) or 0,
            "cache_read_tokens": usage.get("cache_read_tokens", 0) or 0,
            "cache_write_tokens": usage.get("cache_write_tokens", 0) or 0,
            "latency_ms": latency_ms,
            "streamed": streamed,
            "error": error,
            "finish_reason": normalized,
            "finish_reason_raw": raw_finish_reason(finish_reason),
            # Whether the call presented the run's own credential (never
            # the credential, nor anything derived from it).
            "run_credential": getattr(self._inner, "run_credential", None) is not None,
        }
        for key in _CONTEXT_KEYS:
            if self._context.get(key) is not None:
                values[key] = self._context[key]
        try:
            task = asyncio.get_running_loop().create_task(_insert_usage(values))
            collector = _collector.get()
            if collector is not None:
                collector.add(values, task)
        except Exception as e:
            logger.warning(f"Failed to schedule LLM usage recording: {e}")

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        tools: Optional[list[dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> dict[str, Any]:
        start = time.monotonic()
        try:
            response = await self._inner.complete(
                messages=messages,
                model=model,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            )
        except asyncio.CancelledError:
            # Cancelled while waiting on the model API (the job's timeout, a
            # shutdown): recorded as a failed call, then cancelled as asked.
            self._record(
                model=model,
                usage=None,
                latency_ms=int((time.monotonic() - start) * 1000),
                streamed=False,
                error=CANCELLED,
            )
            raise
        except Exception as e:
            typed, hide = self._failure(e, model)
            self._record(
                model=model,
                usage=None,
                latency_ms=int((time.monotonic() - start) * 1000),
                streamed=False,
                error=str(typed or e)[:2000],
            )
            if typed is not None:
                if hide:
                    raise typed from None
                raise typed from e
            raise
        self._record(
            model=model,
            usage=response.get("usage"),
            latency_ms=int((time.monotonic() - start) * 1000),
            streamed=False,
            finish_reason=response.get("finish_reason"),
        )
        return response

    async def stream(
        self,
        messages: list[dict[str, Any]],
        model: str,
        tools: Optional[list[dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> AsyncIterator[dict[str, Any]]:
        start = time.monotonic()
        usage: Optional[dict[str, int]] = None
        error: Optional[str] = None
        finish_reason: Any = None
        try:
            async for chunk in self._inner.stream(
                messages=messages,
                model=model,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            ):
                if chunk.get("usage"):
                    usage = chunk["usage"]
                if chunk.get("finish_reason"):
                    finish_reason = chunk["finish_reason"]
                yield chunk
        except GeneratorExit:
            # Consumer abandoned the stream (e.g. client disconnect);
            # record whatever arrived before the cutoff.
            error = "stream_abandoned"
            raise
        except asyncio.CancelledError:
            # Cancelled while waiting on the model API (the job's timeout, a
            # shutdown): the call failed, whatever arrived before.
            error = CANCELLED
            raise
        except Exception as e:
            typed, hide = self._failure(e, model)
            error = str(typed or e)[:2000]
            if typed is not None:
                if hide:
                    raise typed from None
                raise typed from e
            raise
        finally:
            self._record(
                model=model,
                usage=usage,
                latency_ms=int((time.monotonic() - start) * 1000),
                streamed=True,
                error=error,
                finish_reason=finish_reason,
            )

    def format_tool_calls(self, tool_calls: Any) -> list[dict[str, Any]]:
        return self._inner.format_tool_calls(tool_calls)

    def extract_usage(self, response: Any) -> dict[str, int]:
        return self._inner.extract_usage(response)

    # ── Batch API passthrough ─────────────────────────────────────────────
    # Batch usage is recorded by the batch poller per result (with correct
    # per-chat attribution), not here — a batch is N logical calls, so the
    # single-call recording model doesn't apply.

    @property
    def supports_batch(self) -> bool:  # type: ignore[override]
        return self._inner.supports_batch

    async def submit_batch(self, requests: list[dict[str, Any]]) -> str:
        try:
            return await self._inner.submit_batch(requests)
        except Exception as e:
            typed = self._classify(e)
            if typed is not None:
                raise typed from e
            raise

    async def get_batch_status(self, provider_batch_id: str) -> dict[str, Any]:
        return await self._inner.get_batch_status(provider_batch_id)

    async def fetch_batch_results(self, provider_batch_id: str) -> list[dict[str, Any]]:
        return await self._inner.fetch_batch_results(provider_batch_id)

    async def cancel_batch(self, provider_batch_id: str) -> None:
        await self._inner.cancel_batch(provider_batch_id)
