"""Redis data that used to grow without bound now has a bound.

With the in-cluster Redis on `noeviction`, an unbounded key means write
errors once memory runs out, so:

- the dead-letter queue keeps its newest QUEUE_DLQ_MAX_ENTRIES entries;
- a stream relay keeps about STREAM_RELAY_MAX_ENTRIES events (XADD MAXLEN ~)
  and always has its TTL (XADD and EXPIRE in one MULTI);
- per-chat sets and counters get their TTL in the same MULTI as the write.

These run against the real Redis.
"""

import asyncio
import json
import uuid

import pytest

from app.core.config import settings
from app.core.redis import get_redis
from app.services.queue_service import (
    DLQ_DROPPED_KEY,
    DLQ_KEY,
    push_dead_letter,
    queue_service,
)
from app.services.stream_relay import STREAM_PREFIX, STREAM_TTL, stream_relay


def _entry(i: int) -> str:
    return json.dumps({"job_id": f"job-{i}", "error": "boom", "failed_at": i})


class TestDeadLetterCap:
    async def test_keeps_the_newest_entries(self, monkeypatch):
        monkeypatch.setattr(settings, "queue_dlq_max_entries", 3)
        redis = await get_redis()

        dropped = [await push_dead_letter(redis, _entry(i)) for i in range(5)]

        assert dropped == [0, 0, 0, 1, 1]
        kept = [json.loads(e)["job_id"] for e in await redis.lrange(DLQ_KEY, 0, -1)]
        assert kept == ["job-4", "job-3", "job-2"]  # newest first
        assert int(await redis.get(DLQ_DROPPED_KEY)) == 2

        stats = await queue_service.get_queue_stats()
        assert stats["dlq"] == {"size": 3, "dropped": 2, "max_entries": 3}

        assert (await queue_service.flush_dlq()) == {"flushed": 3}
        assert (await queue_service.get_queue_stats())["dlq"]["dropped"] == 0

    async def test_concurrent_pushes_count_every_drop_once(self, monkeypatch):
        monkeypatch.setattr(settings, "queue_dlq_max_entries", 5)
        redis = await get_redis()

        await asyncio.gather(*(push_dead_letter(redis, _entry(i)) for i in range(40)))

        assert await redis.llen(DLQ_KEY) == 5
        assert int(await redis.get(DLQ_DROPPED_KEY)) == 35

    async def test_zero_means_no_cap(self, monkeypatch):
        monkeypatch.setattr(settings, "queue_dlq_max_entries", 0)
        redis = await get_redis()
        for i in range(12):
            await push_dead_letter(redis, _entry(i))
        assert await redis.llen(DLQ_KEY) == 12
        assert await redis.get(DLQ_DROPPED_KEY) is None

    async def test_dropped_entries_are_not_requeueable_but_kept_ones_are_listed(self, monkeypatch):
        monkeypatch.setattr(settings, "queue_dlq_max_entries", 2)
        redis = await get_redis()
        for i in range(4):
            await push_dead_letter(redis, _entry(i))
        listed = [e["job_id"] for e in await queue_service.get_dlq_entries()]
        assert listed == ["job-3", "job-2"]
        with pytest.raises(ValueError, match="not found in DLQ"):
            await queue_service.retry_dlq_job("job-0")


async def _publish(channel, n, **kwargs):
    for i in range(n):
        await stream_relay.publish(channel, {"type": "chunk", "content": f"c{i}"}, **kwargs)


class TestStreamRelayBounds:
    async def test_a_long_stream_is_trimmed_approximately(self, monkeypatch):
        monkeypatch.setattr(settings, "stream_relay_max_entries", 100)
        channel = f"t-{uuid.uuid4().hex[:8]}"
        await _publish(channel, 1000)

        redis = await get_redis()
        length = await redis.xlen(f"{STREAM_PREFIX}{channel}")
        # MAXLEN ~ trims whole radix-tree nodes (100 entries by default):
        # at least the cap is kept, never much more.
        assert 100 <= length <= 300
        newest = await redis.xrevrange(f"{STREAM_PREFIX}{channel}", count=1)
        assert json.loads(newest[0][1]["data"])["content"] == "c999"

    async def test_zero_means_no_cap(self, monkeypatch):
        monkeypatch.setattr(settings, "stream_relay_max_entries", 0)
        channel = f"t-{uuid.uuid4().hex[:8]}"
        await _publish(channel, 400)
        redis = await get_redis()
        assert await redis.xlen(f"{STREAM_PREFIX}{channel}") == 400

    async def test_every_stream_has_its_ttl(self):
        redis = await get_redis()
        short, kept = f"t-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
        await stream_relay.publish(short, {"type": "chunk"})
        await stream_relay.publish_done(short)
        await stream_relay.publish(kept, {"type": "chunk"}, ttl=86400)

        assert 0 < await redis.ttl(f"{STREAM_PREFIX}{short}") <= STREAM_TTL
        assert STREAM_TTL < await redis.ttl(f"{STREAM_PREFIX}{kept}") <= 86400

    async def test_replay_from_the_start_ends_on_done(self, monkeypatch):
        """A reconnect replaying a trimmed turn (last_id=0) gets its tail
        and still ends on the terminal event."""
        monkeypatch.setattr(settings, "stream_relay_max_entries", 100)
        channel = f"t-{uuid.uuid4().hex[:8]}"
        await _publish(channel, 600)
        await stream_relay.publish_done(channel)

        events = [e async for e in stream_relay.subscribe(channel, last_id="0", timeout=5)]
        assert events[-1]["type"] == "done"
        assert events[-2]["content"] == "c599"
        assert 100 <= len(events) <= 301

    async def test_resume_from_the_last_id_misses_nothing(self, monkeypatch):
        """A client resuming from the id it last saw gets every later event,
        however much older history was trimmed meanwhile."""
        monkeypatch.setattr(settings, "stream_relay_max_entries", 100)
        channel = f"t-{uuid.uuid4().hex[:8]}"
        await _publish(channel, 50)
        seen = await stream_relay.last_entry_id(channel)
        await _publish(channel, 80)  # still within the cap after `seen`
        await stream_relay.publish_done(channel)

        events = [e async for e in stream_relay.subscribe(channel, last_id=seen, timeout=5)]
        assert [e.get("content") for e in events[:-1]] == [f"c{i}" for i in range(80)]
        assert events[-1]["type"] == "done"

    async def test_a_live_subscriber_sees_every_event(self, monkeypatch):
        """An in-flight turn: the subscriber keeps up while the producer
        writes far past the cap; trimming only drops what it already read."""
        monkeypatch.setattr(settings, "stream_relay_max_entries", 100)
        channel = f"t-{uuid.uuid4().hex[:8]}"
        await stream_relay.publish(channel, {"type": "status"})

        async def produce():
            for i in range(500):
                await stream_relay.publish(channel, {"type": "chunk", "content": f"c{i}"})
                if i % 20 == 0:  # a model streams; it doesn't burst 500 at once
                    await asyncio.sleep(0.005)
            await stream_relay.publish_done(channel)

        async def consume():
            return [e async for e in stream_relay.subscribe(channel, last_id="0", timeout=10)]

        events, _ = await asyncio.gather(consume(), produce())
        chunks = [e["content"] for e in events if e.get("type") == "chunk"]
        assert chunks == [f"c{i}" for i in range(500)]
        assert events[-1]["type"] == "done"


class TestPerChatKeysGetTheirTtl:
    async def test_session_grants(self):
        from app.services import approval_rules

        chat_id = str(uuid.uuid4())
        await approval_rules.add_session_grant(chat_id, "send_email")
        redis = await get_redis()
        assert await redis.ttl(f"chat:tool-grant:{chat_id}") > 0

    async def test_injection_counter(self):
        from app.services import chat_steering

        chat_id = str(uuid.uuid4())
        await chat_steering.note_injection(chat_id)
        redis = await get_redis()
        assert await redis.ttl(f"chat:injected:{chat_id}") > 0
