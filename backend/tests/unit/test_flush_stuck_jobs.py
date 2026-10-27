"""POST /system/flush-stuck-jobs: cancel jobs stuck in "running".

The shared Redis client decodes responses, so SCAN yields str keys; the
sweep called `key.decode()` on them and raised AttributeError on the first
stuck job it found (a status record without a job_id field — every one).
"""

import json
import time

import pytest

from app.services import queue_service as qs
from app.services.queue_service import JOB_STATUS_PREFIX, queue_service


class _ScanRedis:
    """SCAN/MGET over a dict, returning keys as the given type."""

    def __init__(self, records: dict[str, dict], as_bytes: bool = False):
        self.records = records
        self.as_bytes = as_bytes

    async def scan(self, cursor, match=None, count=None):
        keys = [k.encode() if self.as_bytes else k for k in self.records]
        return 0, keys

    async def mget(self, *keys):
        return [
            json.dumps(self.records[k.decode() if isinstance(k, bytes) else k]) for k in keys
        ]


@pytest.fixture
def cancelled(monkeypatch):
    seen: list[str] = []

    async def cancel_job(job_id):
        seen.append(job_id)
        return {"status": "cancelled", "job_id": job_id}

    monkeypatch.setattr(queue_service, "cancel_job", cancel_job)
    return seen


def _records(now: float) -> dict[str, dict]:
    return {
        f"{JOB_STATUS_PREFIX}stuck-1": {"status": "running", "enqueued_at": now - 3 * 3600},
        f"{JOB_STATUS_PREFIX}stuck-2": {"status": "running", "enqueued_at": now - 5 * 3600},
        f"{JOB_STATUS_PREFIX}fresh": {"status": "running", "enqueued_at": now - 60},
        f"{JOB_STATUS_PREFIX}done": {"status": "completed", "enqueued_at": now - 9 * 3600},
    }


@pytest.mark.parametrize("as_bytes", [False, True], ids=["str-keys", "bytes-keys"])
async def test_cancels_only_old_running_jobs(monkeypatch, cancelled, as_bytes):
    redis = _ScanRedis(_records(time.time()), as_bytes=as_bytes)

    async def get_redis():
        return redis

    monkeypatch.setattr(qs, "get_redis", get_redis)

    result = await queue_service.flush_stuck_jobs(max_age_hours=2)

    assert result == {"cancelled": 2, "max_age_hours": 2}
    # The job id comes from the key, without the prefix.
    assert sorted(cancelled) == ["stuck-1", "stuck-2"]


async def test_job_id_field_wins_over_the_key(monkeypatch, cancelled):
    redis = _ScanRedis({
        f"{JOB_STATUS_PREFIX}k": {"status": "running", "enqueued_at": 1.0, "job_id": "from-field"},
    })

    async def get_redis():
        return redis

    monkeypatch.setattr(qs, "get_redis", get_redis)

    await queue_service.flush_stuck_jobs()
    assert cancelled == ["from-field"]


async def test_against_real_redis(cancelled):
    """The decode_responses client the app actually uses."""
    from app.core.redis import get_redis

    redis = await get_redis()
    now = time.time()
    for key, record in _records(now).items():
        await redis.set(key, json.dumps(record), ex=600)

    result = await queue_service.flush_stuck_jobs(max_age_hours=2)

    assert result["cancelled"] == 2
    assert sorted(cancelled) == ["stuck-1", "stuck-2"]
