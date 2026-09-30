"""Usage-tracking wrapper around LLM providers.

Every provider returned by ``create_provider()`` is wrapped in
:class:`UsageTrackingProvider`, which records one ``llm_usage`` row per
LLM call (token counts, latency, attribution). Recording is fire-and-forget
on a dedicated DB session: a failed insert never blocks or fails the call,
and it is isolated from whatever transaction state the caller's session
is in.
"""
import asyncio
import logging
import time
from collections.abc import AsyncIterator
from typing import Any, Optional

from .base import BaseLLMProvider

logger = logging.getLogger(__name__)

# Context keys recognized on llm_usage rows (all optional):
# user_id, chat_id, message_id, agent, source
_CONTEXT_KEYS = ("user_id", "chat_id", "message_id", "agent", "source")


async def _insert_usage(values: dict[str, Any]) -> None:
    try:
        from app.core.database import AsyncSessionLocal
        from app.models import LLMUsage

        async with AsyncSessionLocal() as session:
            session.add(LLMUsage(**values))
            await session.commit()
    except Exception as e:
        logger.warning(f"Failed to record LLM usage: {e}")


class UsageTrackingProvider(BaseLLMProvider):
    """Delegates to an inner provider and records token usage per call."""

    def __init__(
        self,
        inner: BaseLLMProvider,
        provider_name: Optional[str] = None,
        provider_type: Optional[str] = None,
        context: Optional[dict[str, Any]] = None,
    ):
        super().__init__(api_key=inner.api_key, base_url=inner.base_url)
        self._inner = inner
        self._provider_name = provider_name
        self._provider_type = provider_type
        self._context = context or {}

    def _record(
        self,
        *,
        model: Optional[str],
        usage: Optional[dict[str, int]],
        latency_ms: int,
        streamed: bool,
        error: Optional[str] = None,
    ) -> None:
        usage = usage or {}
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
        }
        for key in _CONTEXT_KEYS:
            if self._context.get(key) is not None:
                values[key] = self._context[key]
        try:
            asyncio.get_running_loop().create_task(_insert_usage(values))
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
        except Exception as e:
            self._record(
                model=model,
                usage=None,
                latency_ms=int((time.monotonic() - start) * 1000),
                streamed=False,
                error=str(e)[:2000],
            )
            raise
        self._record(
            model=model,
            usage=response.get("usage"),
            latency_ms=int((time.monotonic() - start) * 1000),
            streamed=False,
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
                yield chunk
        except GeneratorExit:
            # Consumer abandoned the stream (e.g. client disconnect);
            # record whatever arrived before the cutoff.
            error = "stream_abandoned"
            raise
        except Exception as e:
            error = str(e)[:2000]
            raise
        finally:
            self._record(
                model=model,
                usage=usage,
                latency_ms=int((time.monotonic() - start) * 1000),
                streamed=True,
                error=error,
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
        return await self._inner.submit_batch(requests)

    async def get_batch_status(self, provider_batch_id: str) -> dict[str, Any]:
        return await self._inner.get_batch_status(provider_batch_id)

    async def fetch_batch_results(self, provider_batch_id: str) -> list[dict[str, Any]]:
        return await self._inner.fetch_batch_results(provider_batch_id)

    async def cancel_batch(self, provider_batch_id: str) -> None:
        await self._inner.cancel_batch(provider_batch_id)
