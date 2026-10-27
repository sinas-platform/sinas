"""Redis Streams-based relay for SSE event forwarding.

Uses XADD/XREAD instead of pub/sub, allowing reconnecting clients to read from
any position (no message loss on disconnect).
"""
import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any, Optional

from app.core.config import settings
from app.core.redis import get_redis
from app.core.telemetry import untraced

logger = logging.getLogger(__name__)

STREAM_PREFIX = "sinas:stream:"
STREAM_TTL = 3600  # 1 hour TTL for stream keys
STREAM_TTL_KEEP_ALIVE = 86400  # 24 hour TTL for keep_alive streams
SUBSCRIBE_WAIT_TIMEOUT = 120  # Max seconds to wait for stream to appear


class StreamRelay:
    """Publish and subscribe to Redis Streams for SSE event relay."""

    async def publish(self, channel_id: str, event: dict[str, Any], ttl: Optional[int] = None) -> str:
        """
        Publish an event to a Redis Stream.

        Args:
            channel_id: The stream channel to publish to
            event: The event data to publish
            ttl: Optional TTL override in seconds (default: STREAM_TTL)

        Returns:
            The stream entry ID (e.g., "1234567890-0")
        """
        redis = await get_redis()
        stream_key = f"{STREAM_PREFIX}{channel_id}"
        max_entries = settings.stream_relay_max_entries

        # Not traced: called per streamed chunk, it would add two Redis
        # spans per token to the turn's trace.
        with untraced():
            # One MULTI: a stream is never left without its TTL (a crash
            # between a separate XADD and EXPIRE kept it forever). The TTL is
            # refreshed on each write, so a finished turn's stream expires
            # `ttl` after its last event. MAXLEN ~ caps one runaway stream;
            # approximate trimming only drops whole old nodes (cheap).
            async with redis.pipeline(transaction=True) as pipe:
                pipe.xadd(
                    stream_key,
                    {"data": json.dumps(event, default=str)},
                    maxlen=max_entries if max_entries > 0 else None,
                    approximate=True,
                )
                pipe.expire(stream_key, ttl or STREAM_TTL)
                entry_id, _ = await pipe.execute()

        return entry_id

    async def publish_done(self, channel_id: str, usage: Optional[dict[str, Any]] = None) -> None:
        """Publish a terminal 'done' event. `usage`: the LLM usage of the run
        that ends here, for an internal subscriber that reports it (the SSE
        relays rebuild their own `done` payload without it)."""
        event: dict[str, Any] = {"type": "done", "status": "completed"}
        if usage is not None:
            event["usage"] = usage
        await self.publish(channel_id, event)

    async def publish_error(self, channel_id: str, error: str, code: Optional[str] = None) -> None:
        """Publish a terminal 'error' event; `code` names a refused LLM call
        (budget_exceeded, rate_limited; app/providers/errors.py)."""
        event: dict[str, Any] = {"type": "error", "error": error}
        if code:
            event["code"] = code
        await self.publish(channel_id, event)

    async def publish_failure(self, channel_id: str, error: BaseException) -> None:
        """Publish a terminal 'error' event for a failed turn: the error
        envelope (`code`, `retryable`, `upstream_status`, ...) of `error`, or
        `internal_error` for an error that isn't an LLM failure
        (app/providers/errors.py)."""
        from app.providers.errors import turn_error

        await self.publish(channel_id, {"type": "error", **turn_error(error).event_fields()})

    async def last_entry_id(self, channel_id: str) -> str:
        """The id of the channel's newest event ("0" when it has none): a
        subscription from here sees only what is published after now."""
        redis = await get_redis()
        newest = await redis.xrevrange(f"{STREAM_PREFIX}{channel_id}", count=1)
        return newest[0][0] if newest else "0"

    async def subscribe(
        self, channel_id: str, last_id: str = "0", timeout: Optional[int] = None
    ) -> AsyncIterator[dict[str, Any]]:
        """
        Subscribe to a Redis Stream from a given position.

        Yields events until a "done" or "error" terminal event is received.
        Waits up to `timeout` seconds for the stream to appear
        (the worker may not have started publishing yet).

        Args:
            channel_id: The stream channel to subscribe to
            last_id: The last received stream entry ID (for reconnection).
                     "0" reads from the beginning, "$" reads only new entries.
            timeout: Max seconds to wait for data. Defaults to SUBSCRIBE_WAIT_TIMEOUT.
        """
        redis = await get_redis()
        stream_key = f"{STREAM_PREFIX}{channel_id}"
        wait_timeout = timeout or SUBSCRIBE_WAIT_TIMEOUT
        last_data_at = time.monotonic()

        while True:
            # XREAD blocks for up to 5 seconds waiting for new entries
            # (not traced: one span per read for as long as a stream lasts).
            with untraced():
                entries = await redis.xread(
                    {stream_key: last_id},
                    count=100,
                    block=5000,
                )

            if not entries:
                idle_time = time.monotonic() - last_data_at
                if idle_time > wait_timeout:
                    logger.error(
                        f"Stream {stream_key} timed out after {wait_timeout}s idle "
                        f"(no data received)"
                    )
                    return
                # Stream may not exist yet — worker is still starting up. Keep waiting.
                continue

            # Reset idle timer whenever we receive data
            last_data_at = time.monotonic()

            for stream_name, messages in entries:
                for entry_id, fields in messages:
                    last_id = entry_id
                    data = json.loads(fields["data"])

                    yield data

                    # Stop on terminal events
                    if data.get("type") in ("done", "error"):
                        return


# Global instance
stream_relay = StreamRelay()
