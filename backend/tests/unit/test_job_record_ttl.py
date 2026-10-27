"""A deferred function job keeps its arq record alive.

arq sets the TTL of `arq:job:<id>` once, at enqueue (expected start + 1
day); `Retry` doesn't refresh it. With QUEUE_SATURATION_TIMEOUT_SECONDS=0
(wait forever) or above about a day, a job deferred for more than 24h was
dropped by arq as "expired" at its next run: no failure recorded, no dead
letter, the execution PENDING forever. Every deferral now pushes the record's
expiry out to the next run + 1 day, and a deferral that finds the record
already gone dead-letters the job instead of deferring it into the void.
"""

import asyncio
import json
import time
import uuid

import pytest
from arq.constants import expires_extra_ms, job_key_prefix
from arq.worker import Retry

from app.core.config import settings
from app.models.execution import ExecutionStatus
from app.services.execution_engine import INFRASTRUCTURE, ExecutionRetryScheduled, executor
from app.services.queue_service import JOB_STATUS_PREFIX
from app.services.shared_admission import SharedPoolSaturated
from tests.unit.test_function_queue_retry import (  # noqa: F401 — fixtures
    _dlq,
    _FakeRedis,
    _function,
    _kwargs,
    _pending_row,
    use_test_db,
)


@pytest.fixture(autouse=True)
def _not_cancelled(monkeypatch):
    async def not_cancelled(execution_id):
        return False

    monkeypatch.setattr("app.queue.worker._execution_cancelled", not_cancelled)


@pytest.fixture
def saturated(monkeypatch):
    async def boom(**kwargs):
        raise SharedPoolSaturated("4/4 slots in use")

    monkeypatch.setattr(executor, "execute_function", boom)


def _job(job_id=None):
    job_id = job_id or f"job-{uuid.uuid4().hex[:8]}"
    return {
        "job_id": job_id, "function_namespace": "default", "function_name": "f",
        "input_data": {}, "execution_id": job_id, "trigger_type": "API",
        "trigger_id": "t", "user_id": str(uuid.uuid4()),
    }


def _extended_ms(redis, job_id):
    return redis.store.get(f"pexpire:{job_key_prefix}{job_id}")


class TestDeferralExtendsTheRecord:
    async def test_saturation_deferral(self, saturated):
        from app.queue import worker

        redis = _FakeRedis()
        kwargs = _job()
        with pytest.raises(Retry) as exc:
            await worker.execute_function_job({"redis": redis, "job_try": 3}, **kwargs)

        defer_ms = exc.value.defer_score
        # The delay to the next run plus arq's own 1-day allowance.
        assert _extended_ms(redis, kwargs["job_id"]) == pytest.approx(
            defer_ms + expires_extra_ms, abs=1
        )

    async def test_wait_forever_past_a_day(self, saturated, monkeypatch):
        """window=0: a job saturated for 30 hours still defers, and keeps
        its record (before: arq dropped it after 24h)."""
        from app.queue import worker

        monkeypatch.setattr(settings, "queue_saturation_timeout_seconds", 0)
        redis = _FakeRedis()
        kwargs = _job()
        redis.store[f"{JOB_STATUS_PREFIX}{kwargs['job_id']}"] = json.dumps(
            {"enqueued_at": time.time() - 30 * 3600}
        )
        with pytest.raises(Retry):
            await worker.execute_function_job({"redis": redis, "job_try": 3000}, **kwargs)
        assert _extended_ms(redis, kwargs["job_id"]) > expires_extra_ms

    async def test_failure_retry(self, monkeypatch):
        from app.queue import worker

        async def engine(**kwargs):
            raise ExecutionRetryScheduled("pod failed", kind=INFRASTRUCTURE, attempt=1, max_attempts=4)

        monkeypatch.setattr(executor, "execute_function", engine)
        redis = _FakeRedis()
        kwargs = _job()
        with pytest.raises(Retry) as exc:
            await worker.execute_function_job({"redis": redis, "job_try": 1}, **kwargs)
        assert _extended_ms(redis, kwargs["job_id"]) == pytest.approx(
            exc.value.defer_score + expires_extra_ms, abs=1
        )

    async def test_redis_error_still_defers(self, saturated):
        """Best effort: a failed extension never costs the deferral."""
        from app.queue import worker

        class _NoPexpire(_FakeRedis):
            async def pexpire(self, key, ms):
                raise ConnectionError("redis blip")

        with pytest.raises(Retry):
            await worker.execute_function_job({"redis": _NoPexpire(), "job_try": 2}, **_job())


@pytest.mark.usefixtures("use_test_db")
class TestRecordAlreadyGone:
    async def test_saturated_job_is_dead_lettered(self, db, test_user, saturated):
        from app.queue import worker

        fn = await _function(db, test_user)
        row = await _pending_row(db, test_user, fn)
        redis = _FakeRedis()
        redis.job_record_exists = False

        with pytest.raises(worker.JobRecordLost):
            await worker.execute_function_job(
                {"redis": redis, "job_try": 4}, **_kwargs(fn, row, test_user)
            )

        (entry,) = _dlq(redis)
        assert entry["job_id"] == row.execution_id
        assert entry["error_type"] == "saturation"
        assert "queue record expired" in entry["error"]
        status = json.loads(redis.store[f"{JOB_STATUS_PREFIX}{row.execution_id}"])
        assert status["status"] == "failed" and status["dead_lettered"] is True
        await db.refresh(row)
        assert row.status == ExecutionStatus.FAILED
        assert "dead-letter queue" in row.error

    async def test_failure_retry_is_dead_lettered(self, db, test_user, monkeypatch):
        from app.queue import worker

        async def engine(**kwargs):
            raise ExecutionRetryScheduled("pod failed", kind=INFRASTRUCTURE, attempt=2, max_attempts=4)

        monkeypatch.setattr(executor, "execute_function", engine)
        fn = await _function(db, test_user)
        row = await _pending_row(db, test_user, fn)
        redis = _FakeRedis()
        redis.job_record_exists = False

        with pytest.raises(worker.JobRecordLost):
            await worker.execute_function_job(
                {"redis": redis, "job_try": 2}, **_kwargs(fn, row, test_user)
            )
        (entry,) = _dlq(redis)
        assert entry["error_type"] == "infrastructure" and "pod failed" in entry["error"]
        await db.refresh(row)
        assert row.status == ExecutionStatus.FAILED


async def test_arq_runs_a_job_deferred_past_its_original_expiry():
    """Against real arq and Redis: a job enqueued with a 1.5s expiry defers
    itself for 2.5s. Without the extension arq drops the re-run as expired;
    with it the second try runs."""
    from arq import func
    from arq.connections import create_pool
    from arq.worker import Worker

    from app.core.redis import get_redis_settings
    from app.queue.worker import _keep_job_key_alive

    queue = f"sinas:test:queue:{uuid.uuid4().hex[:8]}"
    tries: list[int] = []

    async def job(ctx):
        tries.append(ctx["job_try"])
        if ctx["job_try"] == 1:
            assert await _keep_job_key_alive(ctx["redis"], ctx["job_id"], 2.5) is True
            raise Retry(defer=2.5)
        return "ran"

    pool = await create_pool(get_redis_settings())
    try:
        enqueued = await pool.enqueue_job("job", _queue_name=queue, _expires=1.5)
        worker = Worker(
            functions=[func(job, name="job")], redis_pool=pool, queue_name=queue, burst=True,
            poll_delay=0.1, max_tries=3, handle_signals=False,
        )
        await asyncio.wait_for(worker.async_run(), timeout=20)
        assert tries == [1, 2]
        assert await enqueued.result(timeout=5) == "ran"
    finally:
        await pool.aclose()
