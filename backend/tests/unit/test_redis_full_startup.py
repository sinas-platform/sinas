"""Starting up on a full Redis.

An upgrade from an allkeys-lru Redis can start at maxmemory: the DLQ cap
can't trim on push then (Redis refuses the LPUSH, and the MULTI with it),
so every worker and the scheduler trim the DLQ at startup (LTRIM is never
refused outside MULTI) and log loudly when Redis is (nearly) full.
"""

import json

import pytest

from app.core.config import settings
from app.core.redis import get_redis, is_redis_oom, warn_if_redis_nearly_full
from app.services.queue_service import (
    DLQ_KEY,
    push_dead_letter,
    startup_redis_checks,
    trim_dead_letter_queue,
)


def _entry(i):
    return json.dumps({"job_id": f"job-{i}", "input_data": "x" * 200})


class TestDlqOnAFullRedis:
    async def test_push_is_refused_but_startup_trim_works(self, monkeypatch, fill_redis):
        redis = await get_redis()
        monkeypatch.setattr(settings, "queue_dlq_max_entries", 0)  # a pre-0.5.0 DLQ
        for i in range(50):
            await push_dead_letter(redis, _entry(i))
        monkeypatch.setattr(settings, "queue_dlq_max_entries", 10)
        fill_redis.fill()

        with pytest.raises(Exception) as exc:
            await push_dead_letter(redis, _entry(99))
        assert is_redis_oom(exc.value)
        assert await redis.llen(DLQ_KEY) == 50  # the cap couldn't trim

        assert await trim_dead_letter_queue(redis) == 40
        kept = [json.loads(e)["job_id"] for e in await redis.lrange(DLQ_KEY, 0, -1)]
        assert kept == [f"job-{i}" for i in range(49, 39, -1)]  # the newest 10

    async def test_startup_checks_trim_and_say_redis_is_full(self, monkeypatch, fill_redis, caplog):
        redis = await get_redis()
        monkeypatch.setattr(settings, "queue_dlq_max_entries", 0)
        for i in range(5):
            await push_dead_letter(redis, _entry(i))
        monkeypatch.setattr(settings, "queue_dlq_max_entries", 2)
        fill_redis.fill()

        with caplog.at_level("WARNING"):
            await startup_redis_checks(redis, "queue-worker")

        assert await redis.llen(DLQ_KEY) == 2
        messages = [r.getMessage() for r in caplog.records]
        assert any("queue-worker: Redis is FULL" in m and "noeviction" in m for m in messages)


class _Info:
    def __init__(self, used, maxmemory, policy="noeviction"):
        self.memory = {"used_memory": used, "maxmemory": maxmemory, "maxmemory_policy": policy}

    async def info(self, section=None):
        return self.memory


@pytest.mark.parametrize("used,level,expected", [
    (50, None, 50.0),
    (92, "WARNING", 92.0),
    (100, "ERROR", 100.0),
])
async def test_memory_warning_levels(used, level, expected, caplog):
    with caplog.at_level("WARNING", logger="app.core.redis"):
        assert await warn_if_redis_nearly_full(_Info(used, 100), "scheduler") == expected
    levels = [r.levelname for r in caplog.records if "scheduler" in r.getMessage()]
    assert levels == ([level] if level else [])


async def test_no_maxmemory_means_nothing_to_warn_about():
    assert await warn_if_redis_nearly_full(_Info(10**9, 0), "scheduler") is None
