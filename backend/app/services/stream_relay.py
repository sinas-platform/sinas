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

from app.core.redis import get_redis

logger = logging.getLogger(__name__)

STREAM_PREFIX = "sinas:stream:"
STREAM_TTL = 3600  # 1 hour TTL for stream keys
SUBSCRIBE_WAIT_TIMEOUT = 120  # Max seconds to wait for stream to appear


class StreamRelay:
    """Publish and subscribe to Redis Streams for SSE event relay."""

    async def publish(self, channel_id: str, event: dict[str, Any]) -> str:
        """
        Publish an event to a Redis Stream.

        Returns:
            The stream entry ID (e.g., "1234567890-0")
        """
        redis = await get_redis()
        stream_key = f"{STREAM_PREFIX}{channel_id}"

        entry_id = await redis.xadd(
            stream_key,
            {"data": json.dumps(event, default=str)},
        )

        # Set TTL on stream key (refresh on each write)
        await redis.expire(stream_key, STREAM_TTL)

        return entry_id

    async def publish_done(self, channel_id: str) -> None:
        """Publish a terminal 'done' event."""
        await self.publish(channel_id, {"type": "done", "status": "completed"})

    async def publish_error(self, channel_id: str, error: str) -> None:
        """Publish a terminal 'error' event."""
        await self.publish(channel_id, {"type": "error", "error": error})

    async def subscribe(
        self, channel_id: str, last_id: str = "0"
    ) -> AsyncIterator[dict[str, Any]]:
        """
        Subscribe to a Redis Stream from a given position.

        Yields events until a "done" or "error" terminal event is received.
        Waits up to SUBSCRIBE_WAIT_TIMEOUT seconds for the stream to appear
        (the worker may not have started publishing yet).

        Args:
            channel_id: The stream channel to subscribe to
            last_id: The last received stream entry ID (for reconnection).
                     "0" reads from the beginning, "$" reads only new entries.
        """
        redis = await get_redis()
        stream_key = f"{STREAM_PREFIX}{channel_id}"
        started_at = time.monotonic()

        while True:
            # XREAD blocks for up to 5 seconds waiting for new entries
            entries = await redis.xread(
                {stream_key: last_id},
                count=100,
                block=5000,
            )

            if not entries:
                elapsed = time.monotonic() - started_at
                if elapsed > SUBSCRIBE_WAIT_TIMEOUT:
                    logger.error(
                        f"Stream {stream_key} timed out after {SUBSCRIBE_WAIT_TIMEOUT}s"
                    )
                    return
                # Stream may not exist yet — worker is still starting up. Keep waiting.
                continue

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
