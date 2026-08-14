"""Shared-pool saturation is backpressure, not failure.

Regression coverage for the bulk-upload incident: 30 rapid uploads triggered
30 post-upload function jobs onto a 4-slot shared pool; the first 4 ran, the
other 26 failed instantly ("worker pool saturated") with no retry. Saturation
now defers the queue job (arq Retry with backoff) and leaves the Execution
row PENDING until a slot frees or the retry budget runs out.
"""

import contextlib
import json
import uuid
from types import SimpleNamespace

import pytest
from arq.worker import Retry
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Function
from app.models.execution import Execution, ExecutionStatus
from app.services.shared_admission import SharedPoolSaturated


@contextlib.asynccontextmanager
async def _saturated(depth, execution_id):
    raise SharedPoolSaturated("Shared worker pool saturated: 4/4 top-level slots in use")
    yield  # pragma: no cover


class _FakeRedis:
    def __init__(self):
        self.store: dict = {}
        self.published: list = []

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def publish(self, channel, payload):
        self.published.append((channel, payload))


@pytest.fixture
async def shared_fn(db: AsyncSession, test_user):
    fn = Function(
        user_id=test_user.id,
        namespace="default",
        name=f"sat_{uuid.uuid4().hex[:6]}",
        code="def handler(input, context): return 1",
        input_schema={},
        output_schema={},
        is_active=True,
        shared_pool=True,
    )
    db.add(fn)
    await db.commit()
    yield fn
    await db.delete(fn)
    await db.commit()


class TestEngineLeavesRowPending:
    async def test_saturation_propagates_and_row_stays_pending(
        self, db, test_user, shared_fn, monkeypatch
    ):
        monkeypatch.setattr(
            "app.services.shared_admission.shared_pool_admission", _saturated
        )

        @contextlib.asynccontextmanager
        async def fake_session():
            yield db

        monkeypatch.setattr(
            "app.services.execution_engine.AsyncSessionLocal", fake_session
        )
        from app.services.execution_engine import executor

        execution_id = str(uuid.uuid4())
        with pytest.raises(SharedPoolSaturated):
            await executor.execute_function(
                function_namespace=shared_fn.namespace,
                function_name=shared_fn.name,
                input_data={},
                execution_id=execution_id,
                trigger_type="API",
                trigger_id="test",
                user_id=str(test_user.id),
            )

        row = (
            await db.execute(
                select(Execution).where(Execution.execution_id == execution_id)
            )
        ).scalar_one()
        try:
            assert row.status == ExecutionStatus.PENDING  # NOT FAILED
            assert row.error is None
        finally:
            await db.delete(row)
            await db.commit()


def _job_kwargs(execution_id):
    return {
        "job_id": f"job-{uuid.uuid4().hex[:8]}",
        "function_namespace": "default",
        "function_name": "post_upload",
        "input_data": {},
        "execution_id": execution_id,
        "trigger_type": "API",
        "trigger_id": "test",
        "user_id": str(uuid.uuid4()),
    }


class TestWorkerDefersOnSaturation:
    async def test_saturated_job_raises_arq_retry_with_backoff(self, monkeypatch):
        from app.queue import worker

        async def boom(**kwargs):
            raise SharedPoolSaturated("4/4 slots in use")

        monkeypatch.setattr(
            "app.services.execution_engine.executor.execute_function", boom
        )
        redis = _FakeRedis()
        kwargs = _job_kwargs(str(uuid.uuid4()))

        with pytest.raises(Retry) as exc:
            await worker.execute_function_job({"redis": redis, "job_try": 1}, **kwargs)

        assert 2 <= exc.value.defer_score / 1000 <= 5  # 2**1 + jitter(0..2)
        from app.services.queue_service import JOB_STATUS_PREFIX
        status = json.loads(redis.store[f"{JOB_STATUS_PREFIX}{kwargs['job_id']}"])
        assert status["status"] == "queued"
        assert "saturated" in status["detail"] and "retrying" in status["detail"]
        assert redis.published == []  # no failure signal

    async def test_backoff_caps_at_30s(self, monkeypatch):
        from app.queue import worker

        async def boom(**kwargs):
            raise SharedPoolSaturated("4/4")

        monkeypatch.setattr(
            "app.services.execution_engine.executor.execute_function", boom
        )
        with pytest.raises(Retry) as exc:
            await worker.execute_function_job(
                {"redis": _FakeRedis(), "job_try": 10}, **_job_kwargs(str(uuid.uuid4()))
            )
        assert exc.value.defer_score / 1000 <= 32  # 30s cap + jitter

    async def test_exhausted_budget_fails_for_real(self, db, test_user, monkeypatch):
        from app.queue import worker

        execution_id = str(uuid.uuid4())
        row = Execution(
            user_id=test_user.id,
            execution_id=execution_id,
            function_name="default/post_upload",
            trigger_type="API",
            trigger_id="test",
            status=ExecutionStatus.PENDING,
            input_data={},
            started_at=__import__("datetime").datetime.utcnow(),
        )
        db.add(row)
        await db.commit()

        async def boom(**kwargs):
            raise SharedPoolSaturated("4/4")

        monkeypatch.setattr(
            "app.services.execution_engine.executor.execute_function", boom
        )

        @contextlib.asynccontextmanager
        async def fake_session():
            yield db

        monkeypatch.setattr("app.core.database.AsyncSessionLocal", fake_session)

        redis = _FakeRedis()
        kwargs = _job_kwargs(execution_id)
        # Job enqueued far beyond the saturation window -> real failure
        import time as _time
        old = _time.time() - settings.queue_saturation_timeout_seconds - 60
        redis.store[
            f"sinas:job:status:{kwargs['job_id']}"
        ] = json.dumps({"enqueued_at": old})
        with pytest.raises(SharedPoolSaturated):
            await worker.execute_function_job(
                {"redis": redis, "job_try": 5}, **kwargs
            )

        await db.refresh(row)
        try:
            assert row.status == ExecutionStatus.FAILED
            assert "saturated" in (row.error or "").lower() or "4/4" in (row.error or "")
            assert len(redis.published) == 1  # failure IS signalled now
        finally:
            await db.delete(row)
            await db.commit()


class TestWorkerSettings:
    def test_max_tries_covers_saturation_window(self):
        from app.queue.worker import WorkerSettings

        # Worst-case attempts ~= window / 30s backoff cap; arq's cap must
        # never fire before our explicit window check does.
        assert WorkerSettings.max_tries > settings.queue_saturation_timeout_seconds // 30


class TestLongWaitStaysQueued:
    async def test_within_window_defers_even_after_many_attempts(self, monkeypatch):
        """1000-upload scenario: hours of waiting is normal, not failure —
        attempt count must not terminate a job that is still in-window."""
        from app.queue import worker

        async def boom(**kwargs):
            raise SharedPoolSaturated("4/4")

        monkeypatch.setattr(
            "app.services.execution_engine.executor.execute_function", boom
        )
        import time as _time
        redis = _FakeRedis()
        kwargs = _job_kwargs(str(uuid.uuid4()))
        # 40 minutes in, attempt 90 — previously dead at attempt 20
        redis.store[
            f"sinas:job:status:{kwargs['job_id']}"
        ] = json.dumps({"enqueued_at": _time.time() - 2400})
        with pytest.raises(Retry):
            await worker.execute_function_job({"redis": redis, "job_try": 90}, **kwargs)
