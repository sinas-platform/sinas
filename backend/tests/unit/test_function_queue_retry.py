"""Function-queue retry and dead-letter semantics (issue #135).

arq only re-runs a job that raises `Retry`, so the old "re-raise for arq
retry" never retried anything, and the DLQ was only reached when shared-pool
deferrals had pushed arq's job_try past QUEUE_MAX_RETRIES. The policy now:

- the function's own failure is final, unless the function opts in
  (`Function.max_retries`);
- the infrastructure failing before the code ran (`ExecutorUnavailable`)
  retries with backoff up to QUEUE_MAX_RETRIES, then dead-letters;
- saturation deferrals never count as failed attempts;
- a dead-lettered job can be listed and requeued through /queue/dlq.

These tests drive the real worker job and the real engine; only the executor
(and Redis, for the worker) is faked.
"""

import contextlib
import json
import uuid
from datetime import datetime

import pytest
from arq.worker import Retry
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Function
from app.models.execution import Execution, ExecutionStatus
from app.services.execution_engine import (
    INFRASTRUCTURE,
    ExecutionRetryScheduled,
    FunctionExecutionError,
    executor,
)
from app.services.executor.base import ExecutionResult, ExecutorUnavailable
from app.services.queue_service import (
    DLQ_KEY,
    JOB_DONE_CHANNEL_PREFIX,
    JOB_FAILURES_PREFIX,
    JOB_STATUS_PREFIX,
)
from app.services.shared_admission import SharedPoolSaturated
from tests.conftest import auth_headers


class _FakePipeline:
    """Queues commands, then runs them in order on the fake (MULTI/EXEC)."""

    def __init__(self, redis):
        self.redis, self.calls = redis, []

    def __getattr__(self, name):
        def queue(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self

        return queue

    async def execute(self):
        return [await getattr(self.redis, n)(*a, **k) for n, a, k in self.calls]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeRedis:
    """The subset of redis.asyncio the function job uses."""

    def __init__(self):
        self.store: dict = {}
        self.lists: dict = {}
        self.published: list = []

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def delete(self, *keys):
        for key in keys:
            self.store.pop(key, None)

    async def publish(self, channel, payload):
        self.published.append((channel, payload))

    async def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, value)
        return len(self.lists[key])

    async def ltrim(self, key, start, end):
        self.lists[key] = self.lists.get(key, [])[start : end + 1]

    async def incrby(self, key, n):
        self.store[key] = int(self.store.get(key) or 0) + n
        return self.store[key]

    def pipeline(self, transaction=True):
        return _FakePipeline(self)

    # arq's job record (arq:job:<id>), whose expiry a deferral pushes out;
    # it exists unless a test says it expired.
    job_record_exists = True

    async def pexpire(self, key, ms):
        self.store[f"pexpire:{key}"] = ms
        return 1 if self.job_record_exists else 0


class _FakeSandbox:
    """A sandbox executor whose runs have the given outcomes in turn (the
    last one repeats)."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    async def execute(self, **kwargs):
        outcome = self.outcomes[min(self.calls, len(self.outcomes) - 1)]
        self.calls += 1
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@pytest.fixture
def use_test_db(db, monkeypatch):
    """Engine and worker helpers open their own sessions; give them the
    test's (rolled back at the end)."""

    @contextlib.asynccontextmanager
    async def fake_session():
        yield db

    monkeypatch.setattr("app.services.execution_engine.AsyncSessionLocal", fake_session)
    monkeypatch.setattr("app.core.database.AsyncSessionLocal", fake_session)


@pytest.fixture
def sandbox(monkeypatch):
    def install(*outcomes):
        fake = _FakeSandbox(*outcomes)
        monkeypatch.setattr(executor, "_sandbox_executor", fake)
        monkeypatch.setattr(executor, "_sandbox_executor_resolved", True)
        return fake

    return install


async def _function(db: AsyncSession, user, max_retries: int = 0, input_schema=None) -> Function:
    fn = Function(
        user_id=user.id,
        namespace="default",
        name=f"retry_{uuid.uuid4().hex[:8]}",
        code="def handler(input, context): return 1",
        input_schema=input_schema or {},
        output_schema={},
        is_active=True,
        shared_pool=False,
        max_retries=max_retries,
    )
    db.add(fn)
    await db.commit()
    return fn


async def _pending_row(db: AsyncSession, user, fn: Function) -> Execution:
    row = Execution(
        user_id=user.id,
        execution_id=str(uuid.uuid4()),
        function_name=fn.name,
        trigger_type="API",
        trigger_id="test",
        status=ExecutionStatus.PENDING,
        input_data={},
        started_at=datetime.utcnow(),
    )
    db.add(row)
    await db.commit()
    return row


def _kwargs(fn: Function, row: Execution, user, input_data=None) -> dict:
    return {
        "job_id": row.execution_id,
        "function_namespace": fn.namespace,
        "function_name": fn.name,
        "input_data": input_data or {},
        "execution_id": row.execution_id,
        "trigger_type": "API",
        "trigger_id": "test",
        "user_id": str(user.id),
    }


async def _run_until_final(worker, redis, kwargs, max_runs=20):
    """Run the job the way arq does: again after every Retry, job_try + 1.
    Returns the delays asked for and the exception that ended it."""
    delays = []
    for job_try in range(1, max_runs + 1):
        try:
            await worker.execute_function_job({"redis": redis, "job_try": job_try}, **kwargs)
        except Retry as r:
            delays.append(r.defer_score / 1000)
            continue
        except Exception as e:  # noqa: BLE001 — the terminal failure
            return delays, e
        return delays, None
    raise AssertionError("job never finished")


def _dlq(redis) -> list[dict]:
    return [json.loads(raw) for raw in redis.lists.get(DLQ_KEY, [])]


class TestUserCodeFailure:
    async def test_does_not_retry_by_default(self, db, test_user, use_test_db, sandbox):
        from app.queue import worker

        fn = await _function(db, test_user)
        row = await _pending_row(db, test_user, fn)
        fake = sandbox(ExecutionResult.failed("ZeroDivisionError: division by zero"))
        redis = _FakeRedis()

        delays, exc = await _run_until_final(worker, redis, _kwargs(fn, row, test_user))

        assert delays == [] and fake.calls == 1  # ran once, never again
        assert isinstance(exc, FunctionExecutionError) and not exc.dead_letter
        await db.refresh(row)
        assert row.status == ExecutionStatus.FAILED
        assert "division by zero" in row.error and "dead-letter" not in row.error
        assert _dlq(redis) == []  # nothing to requeue: it would fail the same way
        status = json.loads(redis.store[f"{JOB_STATUS_PREFIX}{row.execution_id}"])
        assert status["status"] == "failed" and status["dead_lettered"] is False
        assert [c for c, _ in redis.published] == [f"{JOB_DONE_CHANNEL_PREFIX}{row.execution_id}"]

    async def test_retries_when_the_function_opts_in(self, db, test_user, use_test_db, sandbox):
        from app.queue import worker

        fn = await _function(db, test_user, max_retries=2)
        row = await _pending_row(db, test_user, fn)
        fake = sandbox(ExecutionResult.failed("upstream said 503"))
        redis = _FakeRedis()
        kwargs = _kwargs(fn, row, test_user)

        # First run: retry scheduled, row back to PENDING with the failure noted
        with pytest.raises(Retry):
            await worker.execute_function_job({"redis": redis, "job_try": 1}, **kwargs)
        await db.refresh(row)
        assert row.status == ExecutionStatus.PENDING
        assert row.error.startswith("Attempt 1 of 3 failed, retrying") and "503" in row.error
        assert redis.published == []  # a waiting caller is not told it failed
        status = json.loads(redis.store[f"{JOB_STATUS_PREFIX}{row.execution_id}"])
        assert status["status"] == "queued" and status["attempt"] == 1

        delays, exc = await _run_until_final(worker, redis, kwargs)
        assert fake.calls == 3 and len(delays) == 1  # 1 + 2 retries
        assert isinstance(exc, FunctionExecutionError) and exc.dead_letter and not exc.transient
        (entry,) = _dlq(redis)
        assert entry["error_type"] == "function" and entry["attempts"] == 3
        assert "503" in entry["error"]

    async def test_validation_errors_never_retry(self, db, test_user, use_test_db, sandbox):
        from app.queue import worker

        schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
        fn = await _function(db, test_user, max_retries=3, input_schema=schema)
        row = await _pending_row(db, test_user, fn)
        fake = sandbox(ExecutionResult.failed("never reached"))
        redis = _FakeRedis()

        delays, exc = await _run_until_final(worker, redis, _kwargs(fn, row, test_user))

        assert delays == [] and fake.calls == 0
        assert isinstance(exc, FunctionExecutionError) and not exc.dead_letter
        assert _dlq(redis) == []

    async def test_success_after_a_failed_attempt_clears_the_note(
        self, db, test_user, use_test_db, sandbox
    ):
        from app.queue import worker

        fn = await _function(db, test_user, max_retries=1)
        row = await _pending_row(db, test_user, fn)
        sandbox(ExecutionResult.failed("flaky"))
        redis = _FakeRedis()
        kwargs = _kwargs(fn, row, test_user)
        with pytest.raises(Retry):
            await worker.execute_function_job({"redis": redis, "job_try": 1}, **kwargs)

        sandbox(ExecutionResult.from_wire({"result": 42, "status": "completed"}))
        assert await worker.execute_function_job({"redis": redis, "job_try": 2}, **kwargs) == 42
        await db.refresh(row)
        assert (row.status, row.error, row.output_data) == (ExecutionStatus.COMPLETED, None, 42)
        assert f"{JOB_FAILURES_PREFIX}{row.execution_id}" not in redis.store


class TestSucceededRunsAreNeverRepeated:
    async def test_bookkeeping_error_after_success_keeps_the_result(
        self, db, test_user, use_test_db, sandbox, monkeypatch
    ):
        """The batch update after a committed success fails: the job still
        succeeds — no retry, no second run, row stays COMPLETED."""
        from app.queue import worker

        fn = await _function(db, test_user, max_retries=3)
        row = await _pending_row(db, test_user, fn)
        from app.models.batch import Batch, BatchKind, BatchStatus

        now = datetime.utcnow()
        batch = Batch(
            user_id=test_user.id, kind=BatchKind.FUNCTION, target_namespace=fn.namespace,
            target_name=fn.name, total=1, status=BatchStatus.QUEUED, started_at=now, created_at=now,
        )
        db.add(batch)
        await db.commit()
        row.batch_id = batch.id
        await db.commit()
        fake = sandbox(ExecutionResult.from_wire({"result": 7, "status": "completed"}))

        async def batch_down(**kwargs):
            raise ConnectionResetError("db gone")

        monkeypatch.setattr("app.services.batch_service.on_execution_terminated", batch_down)
        redis = _FakeRedis()
        delays, exc = await _run_until_final(worker, redis, _kwargs(fn, row, test_user))

        assert (delays, exc, fake.calls) == ([], None, 1)
        await db.refresh(row)
        assert row.status == ExecutionStatus.COMPLETED and row.output_data == 7

    async def test_failing_to_save_a_success_does_not_rerun(
        self, db, test_user, use_test_db, sandbox, monkeypatch
    ):
        """Even an opted-in function is not retried when only saving its
        result failed: that would run it twice."""
        from sqlalchemy.exc import OperationalError

        fn = await _function(db, test_user, max_retries=3)
        row = await _pending_row(db, test_user, fn)
        sandbox(ExecutionResult.from_wire({"result": 7, "status": "completed"}))
        real_commit, broken = db.commit, {"once": True}

        async def commit():
            if broken.pop("once", False):
                raise OperationalError("COMMIT", {}, ConnectionResetError("reset"))
            await real_commit()

        async def rollback():
            pass

        monkeypatch.setattr(db, "commit", commit)
        monkeypatch.setattr(db, "rollback", rollback)

        with pytest.raises(FunctionExecutionError) as exc:
            await executor.execute_function(
                function_namespace=fn.namespace, function_name=fn.name, input_data={},
                execution_id=row.execution_id, trigger_type="API", trigger_id="test",
                user_id=str(test_user.id), prior_failures={},
            )
        assert not exc.value.dead_letter  # final; nothing to requeue either


class TestFinalBookkeepingErrors:
    async def test_exhausted_job_still_dead_letters(
        self, db, test_user, use_test_db, sandbox, monkeypatch
    ):
        """The batch update after the final failure errors: the failure's
        flags survive, so the job still lands in the DLQ."""
        from app.models.batch import Batch, BatchKind, BatchStatus
        from app.queue import worker

        monkeypatch.setattr(settings, "queue_max_retries", 0)
        fn = await _function(db, test_user)
        row = await _pending_row(db, test_user, fn)
        now = datetime.utcnow()
        batch = Batch(
            user_id=test_user.id, kind=BatchKind.FUNCTION, target_namespace=fn.namespace,
            target_name=fn.name, total=1, status=BatchStatus.QUEUED, started_at=now, created_at=now,
        )
        db.add(batch)
        await db.commit()
        row.batch_id = batch.id
        await db.commit()
        sandbox(ExecutorUnavailable("No workers available"))

        async def batch_down(**kwargs):
            raise ConnectionResetError("db gone")

        monkeypatch.setattr("app.services.batch_service.on_execution_terminated", batch_down)
        redis = _FakeRedis()
        _, exc = await _run_until_final(worker, redis, _kwargs(fn, row, test_user))

        assert isinstance(exc, FunctionExecutionError) and exc.dead_letter
        (entry,) = _dlq(redis)
        assert entry["error_type"] == "infrastructure"


class TestRetrySchedulingWithoutRedis:
    async def test_retry_is_raised_even_if_status_writes_fail(self, test_user, monkeypatch):
        from app.queue import worker

        async def engine(**kwargs):
            raise ExecutionRetryScheduled(
                "pod failed", kind=INFRASTRUCTURE, attempt=1, max_attempts=4
            )

        monkeypatch.setattr(executor, "execute_function", engine)

        class _FlakyRedis(_FakeRedis):
            async def set(self, key, value, ex=None):
                if JOB_FAILURES_PREFIX in key or '"queued"' in str(value):
                    raise ConnectionError("redis down")
                await super().set(key, value, ex=ex)

        kwargs = {
            "job_id": "j3", "function_namespace": "default", "function_name": "f",
            "input_data": {}, "execution_id": str(uuid.uuid4()), "trigger_type": "API",
            "trigger_id": "t", "user_id": str(test_user.id),
        }
        with pytest.raises(Retry):
            await worker.execute_function_job({"redis": _FlakyRedis(), "job_try": 1}, **kwargs)


class TestInfrastructureFailure:
    async def test_retries_with_backoff_then_dead_letters(
        self, db, test_user, use_test_db, sandbox, monkeypatch
    ):
        from app.queue import worker

        monkeypatch.setattr(settings, "queue_max_retries", 3)
        monkeypatch.setattr(settings, "queue_retry_delay", 10)
        fn = await _function(db, test_user)  # no opt-in needed: the code never ran
        row = await _pending_row(db, test_user, fn)
        fake = sandbox(ExecutorUnavailable("Sandbox container failed to start: no space left"))
        redis = _FakeRedis()

        delays, exc = await _run_until_final(worker, redis, _kwargs(fn, row, test_user))

        assert fake.calls == 4  # 1 + QUEUE_MAX_RETRIES
        # 10s, 20s, 40s (+ up to 20% jitter): exponential backoff
        assert [int(d // 10) * 10 for d in delays] == [10, 20, 40]
        assert all(base <= d <= base * 1.2 for base, d in zip((10, 20, 40), delays))
        assert isinstance(exc, FunctionExecutionError) and exc.transient and exc.dead_letter

        (entry,) = _dlq(redis)
        assert entry["job_id"] == entry["execution_id"] == row.execution_id
        assert entry["error_type"] == "infrastructure" and entry["attempts"] == 4
        assert "no space left" in entry["error"]
        assert entry["function_namespace"] == fn.namespace and entry["user_id"] == str(test_user.id)

        await db.refresh(row)
        assert row.status == ExecutionStatus.FAILED
        assert "no space left" in row.error and "dead-letter queue" in row.error
        assert f"{JOB_FAILURES_PREFIX}{row.execution_id}" not in redis.store

    async def test_database_unreachable_before_the_run_retries(
        self, db, test_user, use_test_db, monkeypatch
    ):
        """A transient error while preparing the run (nothing has run yet)."""
        from app.queue import worker

        monkeypatch.setattr(settings, "queue_max_retries", 1)
        fn = await _function(db, test_user)
        row = await _pending_row(db, test_user, fn)

        async def down(*args, **kwargs):
            raise ConnectionRefusedError("connection refused")

        monkeypatch.setattr(executor, "load_function", down)
        callbacks = []

        async def fire_callback(**kwargs):
            callbacks.append(kwargs)

        monkeypatch.setattr("app.services.execution_engine._fire_callback", fire_callback)
        redis = _FakeRedis()
        kwargs = {**_kwargs(fn, row, test_user), "callback_url": "https://app.example/cb"}
        delays, exc = await _run_until_final(worker, redis, kwargs)

        assert len(delays) == 1
        assert isinstance(exc, ExecutorUnavailable)
        (entry,) = _dlq(redis)
        assert entry["error_type"] == "infrastructure" and entry["attempts"] == 2
        assert entry["callback_url"] == "https://app.example/cb"
        await db.refresh(row)
        assert row.status == ExecutionStatus.FAILED and "dead-letter" in row.error
        # The caller is told once, at the end — not per attempt
        (cb,) = callbacks
        assert cb["status"] == "failure" and "connection refused" in cb["error"]
        assert cb["callback_url"] == "https://app.example/cb"

    async def test_outages_do_not_use_up_the_functions_retries(
        self, db, test_user, use_test_db, sandbox
    ):
        """Infrastructure and function failures have separate budgets: an
        outage before the first real run leaves max_retries=1 intact."""
        from app.queue import worker

        fn = await _function(db, test_user, max_retries=1)
        row = await _pending_row(db, test_user, fn)
        fake = sandbox(
            ExecutorUnavailable("No workers available"),
            ExecutionResult.failed("upstream 503"),
        )
        redis = _FakeRedis()

        delays, exc = await _run_until_final(worker, redis, _kwargs(fn, row, test_user))

        # outage (retried), code failure (its one retry), code failure (final)
        assert fake.calls == 3 and len(delays) == 2
        assert isinstance(exc, FunctionExecutionError) and exc.dead_letter and not exc.transient
        (entry,) = _dlq(redis)
        assert entry["error_type"] == "function" and entry["attempts"] == 3

    async def test_admission_outage_before_dispatch_retries(
        self, db, test_user, use_test_db, monkeypatch
    ):
        """The shared-pool admission gate losing Redis happens before the
        code is dispatched: an infrastructure failure, not the function's."""
        from redis.exceptions import ConnectionError as RedisConnectionError

        from app.queue import worker

        monkeypatch.setattr(settings, "queue_max_retries", 1)
        fn = await _function(db, test_user, max_retries=0)
        fn.shared_pool = True
        await db.commit()
        row = await _pending_row(db, test_user, fn)

        @contextlib.asynccontextmanager
        async def redis_down(depth, execution_id):
            raise RedisConnectionError("Error 111 connecting to redis:6379")
            yield  # pragma: no cover

        monkeypatch.setattr("app.services.shared_admission.shared_pool_admission", redis_down)
        redis = _FakeRedis()
        delays, exc = await _run_until_final(worker, redis, _kwargs(fn, row, test_user))

        assert len(delays) == 1
        assert isinstance(exc, FunctionExecutionError) and exc.transient and exc.dead_letter
        (entry,) = _dlq(redis)
        assert entry["error_type"] == "infrastructure"

    def test_unrelated_errors_are_not_transient(self):
        from app.services.executor.base import is_transient_infra_error

        assert is_transient_infra_error(ConnectionRefusedError())
        assert not is_transient_infra_error(ValueError("bad input"))
        assert not is_transient_infra_error(KeyError("x"))

    async def test_executor_preparation_outage_is_unavailable(self):
        """An executor loading the function (or secrets, or the image) before
        it runs anything: a lost connection there is ExecutorUnavailable."""
        from sqlalchemy.exc import OperationalError

        from app.services.executor.base import before_run

        with pytest.raises(ExecutorUnavailable, match="Loading the function"):
            async with before_run("Loading the function"):
                raise OperationalError("SELECT 1", {}, ConnectionResetError("reset"))
        with pytest.raises(ValueError):  # anything else passes through
            async with before_run("Loading the function"):
                raise ValueError("bad")

    async def test_retry_scheduled_even_when_the_session_is_broken(
        self, db, test_user, use_test_db, sandbox, monkeypatch
    ):
        """The failure took the session's connection with it: the retry note
        goes through a fresh session and the retry is scheduled regardless."""
        from sqlalchemy.exc import OperationalError

        fn = await _function(db, test_user)
        row = await _pending_row(db, test_user, fn)
        sandbox(ExecutorUnavailable("Sandbox container failed to start: db gone"))

        real_commit, broken = db.commit, {"once": True}

        async def commit():
            if broken.pop("once", False):
                raise OperationalError("COMMIT", {}, ConnectionResetError("reset"))
            await real_commit()

        async def rollback():  # the test's outer transaction must survive
            pass

        monkeypatch.setattr(db, "commit", commit)
        monkeypatch.setattr(db, "rollback", rollback)

        with pytest.raises(ExecutionRetryScheduled) as exc:
            await executor.execute_function(
                function_namespace=fn.namespace, function_name=fn.name, input_data={},
                execution_id=row.execution_id, trigger_type="API", trigger_id="test",
                user_id=str(test_user.id), prior_failures={},
            )
        assert exc.value.kind == "infrastructure"
        await db.refresh(row)
        assert row.status == ExecutionStatus.PENDING and row.error.startswith("Attempt 1 of")


class TestSaturationDeferrals:
    async def test_deferrals_do_not_consume_attempts(self, test_user, monkeypatch):
        """Many deferrals, then an ordinary failure: final, but not
        dead-lettered (it used to be, once deferrals pushed job_try past
        QUEUE_MAX_RETRIES), and the engine saw no failed attempts."""
        from app.queue import worker

        seen = []
        calls = {"n": 0}

        async def engine(**kwargs):
            seen.append(dict(kwargs["prior_failures"]))
            calls["n"] += 1
            if calls["n"] <= 12:
                raise SharedPoolSaturated("4/4 slots in use")
            err = FunctionExecutionError("Function execution failed: boom")
            err.recorded = True
            raise err

        monkeypatch.setattr(executor, "execute_function", engine)

        async def not_cancelled(execution_id):
            return False

        monkeypatch.setattr(worker, "_execution_cancelled", not_cancelled)
        redis = _FakeRedis()
        kwargs = {
            "job_id": "j1", "function_namespace": "default", "function_name": "f",
            "input_data": {}, "execution_id": str(uuid.uuid4()), "trigger_type": "API",
            "trigger_id": "t", "user_id": str(test_user.id),
        }
        delays, exc = await _run_until_final(worker, redis, kwargs, max_runs=30)

        assert len(delays) == 12 and seen == [{}] * 13
        assert isinstance(exc, FunctionExecutionError)
        assert _dlq(redis) == []
        assert f"{JOB_FAILURES_PREFIX}j1" not in redis.store

    async def test_a_failure_after_deferrals_still_gets_its_retries(self, test_user, monkeypatch):
        from app.queue import worker

        monkeypatch.setattr(settings, "queue_max_retries", 2)
        seen = []

        async def engine(**kwargs):
            prior = kwargs["prior_failures"].get(INFRASTRUCTURE, 0)
            seen.append(prior)
            if len(seen) <= 5:
                raise SharedPoolSaturated("4/4 slots in use")
            n = prior + 1
            if n <= settings.queue_max_retries:
                raise ExecutionRetryScheduled(
                    "pod failed", kind=INFRASTRUCTURE, attempt=n, max_attempts=3
                )
            err = FunctionExecutionError("Function execution failed: pod failed")
            err.transient = err.dead_letter = err.recorded = True
            raise err

        monkeypatch.setattr(executor, "execute_function", engine)

        async def not_cancelled(execution_id):
            return False

        monkeypatch.setattr(worker, "_execution_cancelled", not_cancelled)
        redis = _FakeRedis()
        kwargs = {
            "job_id": "j2", "function_namespace": "default", "function_name": "f",
            "input_data": {}, "execution_id": str(uuid.uuid4()), "trigger_type": "API",
            "trigger_id": "t", "user_id": str(test_user.id),
        }
        _, exc = await _run_until_final(worker, redis, kwargs, max_runs=30)

        # job_try reached 8, but the failed-attempt count only 2
        assert seen == [0, 0, 0, 0, 0, 0, 1, 2]
        (entry,) = _dlq(redis)
        assert entry["attempts"] == 3


class TestCancelledWhileWaiting:
    async def test_a_cancelled_execution_is_not_rerun(self, db, test_user, use_test_db, sandbox):
        from app.queue import worker

        fn = await _function(db, test_user, max_retries=2)
        row = await _pending_row(db, test_user, fn)
        fake = sandbox(ExecutionResult.failed("flaky"))
        redis = _FakeRedis()
        kwargs = _kwargs(fn, row, test_user)
        with pytest.raises(Retry):
            await worker.execute_function_job({"redis": redis, "job_try": 1}, **kwargs)

        row.status = ExecutionStatus.CANCELLED  # e.g. its batch was cancelled
        await db.commit()
        assert await worker.execute_function_job({"redis": redis, "job_try": 2}, **kwargs) is None
        assert fake.calls == 1
        status = json.loads(redis.store[f"{JOB_STATUS_PREFIX}{row.execution_id}"])
        assert status["status"] == "cancelled"


class TestDeadLetterQueueApi:
    async def test_list_and_requeue(self, client, db, admin_user, test_user, use_test_db, monkeypatch):
        """A dead-lettered job is listed with its error and can be requeued —
        under the same id, although arq still holds the failed run's result
        (it used to refuse that silently)."""
        from arq.connections import create_pool
        from arq.constants import result_key_prefix

        from app.core.redis import get_redis, get_redis_settings

        fn = await _function(db, test_user)
        row = await _pending_row(db, test_user, fn)
        row.status, row.error = ExecutionStatus.FAILED, "boom (moved to the dead-letter queue)"
        await db.commit()
        eid = row.execution_id

        redis = await get_redis()
        pool = await create_pool(get_redis_settings())
        monkeypatch.setattr("app.services.queue_service.get_arq_pool", lambda: _ret(pool))
        try:
            entry = {
                **_kwargs(fn, row, test_user),
                "function": f"{fn.namespace}/{fn.name}",
                "callback_url": None, "depth": 0, "chat_id": None,
                "error": "Sandbox container failed to start", "error_type": "infrastructure",
                "attempts": 4, "failed_at": 1.0,
                "credential_context": {"orgId": "org-1"},
            }
            await redis.lpush(DLQ_KEY, json.dumps(entry))
            await redis.set(f"{result_key_prefix}{eid}", b"failed-run", ex=3600)
            await redis.set(f"{JOB_FAILURES_PREFIX}{eid}", "4")
            h = auth_headers(admin_user)

            r = await client.get("/api/v1/queue/dlq", headers=h)
            assert r.status_code == 200
            (listed,) = [e for e in r.json() if e["job_id"] == eid]
            assert listed["error"] == "Sandbox container failed to start"
            assert (listed["error_type"], listed["attempts"]) == ("infrastructure", 4)

            r = await client.post(f"/api/v1/queue/dlq/{eid}/retry", headers=h)
            assert r.status_code == 200, r.text
            assert r.json()["new_job_id"] == eid

            # Requeued with the original request's attribution context, not
            # the requeueing admin's
            from arq.jobs import Job

            info = await Job(eid, pool).info()
            assert info.kwargs["credential_context"] == {"orgId": "org-1"}

            # Really queued for arq, out of the DLQ, failure count reset
            assert await redis.zscore("sinas:queue:functions", eid) is not None
            assert all(json.loads(e)["job_id"] != eid for e in await redis.lrange(DLQ_KEY, 0, -1))
            assert await redis.get(f"{JOB_FAILURES_PREFIX}{eid}") is None
            await db.refresh(row)
            assert (row.status, row.error) == (ExecutionStatus.PENDING, None)

            # Requeueing again while it is queued is refused, not lost, and
            # leaves the active job's state alone
            await redis.lpush(DLQ_KEY, json.dumps(entry))
            await redis.set(f"{JOB_FAILURES_PREFIX}{eid}", json.dumps({"function": 1}))
            row.error = "Attempt 1 of 2 failed, retrying: boom"
            await db.commit()
            r = await client.post(f"/api/v1/queue/dlq/{eid}/retry", headers=h)
            assert r.status_code == 409
            assert len(await redis.lrange(DLQ_KEY, 0, -1)) == 1
            assert await redis.get(f"{JOB_FAILURES_PREFIX}{eid}") is not None
            await db.refresh(row)
            assert row.error == "Attempt 1 of 2 failed, retrying: boom"

            # One requeue at a time
            from app.services.queue_service import DLQ_REQUEUE_LOCK_PREFIX

            await redis.set(f"{DLQ_REQUEUE_LOCK_PREFIX}{eid}", "1")
            r = await client.post(f"/api/v1/queue/dlq/{eid}/retry", headers=h)
            assert r.status_code == 409 and "already being requeued" in r.text
        finally:
            await pool.aclose()

    async def test_requeue_reopens_a_finished_batch(
        self, client, db, admin_user, test_user, use_test_db, monkeypatch
    ):
        from arq.connections import create_pool

        from app.core.redis import get_redis, get_redis_settings
        from app.models.batch import Batch, BatchKind, BatchStatus

        fn = await _function(db, test_user)
        now = datetime.utcnow()
        batch = Batch(
            user_id=test_user.id, kind=BatchKind.FUNCTION, target_namespace=fn.namespace,
            target_name=fn.name, total=1, status=BatchStatus.FAILED, started_at=now,
            finished_at=now, created_at=now,
        )
        db.add(batch)
        await db.commit()
        row = await _pending_row(db, test_user, fn)
        row.status, row.batch_id = ExecutionStatus.FAILED, batch.id
        await db.commit()

        redis = await get_redis()
        pool = await create_pool(get_redis_settings())
        try:
            entry = {**_kwargs(fn, row, test_user), "error": "x", "error_type": "infrastructure"}
            await redis.lpush(DLQ_KEY, json.dumps(entry))

            # Enqueueing fails: execution and batch are put back as they were
            class _DownPool:
                async def enqueue_job(self, *args, **kwargs):
                    raise ConnectionError("redis down")

            monkeypatch.setattr(
                "app.services.queue_service.get_arq_pool", lambda: _ret(_DownPool())
            )
            from app.services.queue_service import queue_service

            with pytest.raises(ConnectionError):
                await queue_service.retry_dlq_job(row.execution_id)
            await db.refresh(batch)
            await db.refresh(row)
            assert batch.status == BatchStatus.FAILED and batch.finished_at is not None
            assert row.status == ExecutionStatus.FAILED
            assert len(await redis.lrange(DLQ_KEY, 0, -1)) == 1  # kept for another try
            # No status key claims it queued (the old one had expired)
            assert await redis.get(f"{JOB_STATUS_PREFIX}{row.execution_id}") is None

            monkeypatch.setattr("app.services.queue_service.get_arq_pool", lambda: _ret(pool))
            r = await client.post(
                f"/api/v1/queue/dlq/{row.execution_id}/retry", headers=auth_headers(admin_user)
            )
            assert r.status_code == 200, r.text
            await db.refresh(batch)
            # Reopened: its terminal status and callback follow the requeued run
            assert (batch.status, batch.finished_at) == (BatchStatus.RUNNING, None)
        finally:
            await pool.aclose()


class TestFailedRequeueRestoresBatch:
    async def test_judged_by_the_children_now(self, db, test_user, use_test_db):
        """A failed requeue closes the batch it reopened again — unless
        another child was requeued meanwhile (still in flight: the batch
        stays open; finished differently: the batch finishes afresh)."""
        from app.models.batch import Batch, BatchKind, BatchStatus
        from app.services.queue_service import queue_service

        fn = await _function(db, test_user)
        now = datetime.utcnow()
        batch = Batch(
            user_id=test_user.id, kind=BatchKind.FUNCTION, target_namespace=fn.namespace,
            target_name=fn.name, total=2, status=BatchStatus.FAILED, started_at=now,
            finished_at=now, created_at=now,
        )
        db.add(batch)
        await db.commit()
        a, b = await _pending_row(db, test_user, fn), await _pending_row(db, test_user, fn)
        for row in (a, b):
            row.status, row.batch_id = ExecutionStatus.FAILED, batch.id
        await db.commit()

        # A's requeue reopens the batch, B is requeued meanwhile (in flight)
        snapshot = await queue_service._reset_execution_for_requeue(a.execution_id)
        b.status = ExecutionStatus.PENDING
        await db.commit()
        await queue_service._restore_after_failed_requeue(a.execution_id, snapshot)
        await db.refresh(batch)
        await db.refresh(a)
        assert a.status == ExecutionStatus.FAILED
        assert (batch.status, batch.finished_at) == (BatchStatus.RUNNING, None)  # B will close it
        from app.services import batch_service

        b.status = ExecutionStatus.FAILED
        await db.commit()
        await batch_service.on_execution_terminated(db=db, batch_id=batch.id)
        await db.refresh(batch)
        assert batch.status == BatchStatus.FAILED and batch.finished_at is not None

        # Same again, but B already succeeded: finished afresh, with B's outcome
        snapshot = await queue_service._reset_execution_for_requeue(a.execution_id)
        b.status = ExecutionStatus.COMPLETED
        await db.commit()
        await queue_service._restore_after_failed_requeue(a.execution_id, snapshot)
        await db.refresh(batch)
        assert batch.status == BatchStatus.PARTIAL and batch.finished_at is not None

        # Nothing else changed: the old status and finished_at come back
        finished = batch.finished_at
        snapshot = await queue_service._reset_execution_for_requeue(a.execution_id)
        await queue_service._restore_after_failed_requeue(a.execution_id, snapshot)
        await db.refresh(batch)
        assert (batch.status, batch.finished_at) == (BatchStatus.PARTIAL, finished)


async def _ret(value):
    return value


class TestStaleResults:
    def test_k8s_wait_script_clears_a_stale_result_before_triggering(self):
        """A retry or requeue reuses the execution id; a result file left by
        the earlier run must never be read back as the new run's."""
        from app.services.executor._wire import build_wait_script

        script = build_wait_script("e1", 5, write_trigger=True)
        assert script.index('os.remove("/tmp/exec_result_e1.json")') < script.index(
            'open("/tmp/exec_trigger_e1", "w")'
        )


class TestWorkerSettings:
    def test_arq_backstop_covers_deferrals_and_retries(self):
        from app.queue.worker import WorkerSettings
        from app.schemas.spec.function import MAX_FUNCTION_RETRIES

        # Deferrals + both retry budgets (a job can use up each) + the last run
        floor = (
            settings.queue_saturation_timeout_seconds // 30
            + settings.queue_max_retries
            + MAX_FUNCTION_RETRIES
        )
        assert WorkerSettings.max_tries > floor
        assert not hasattr(WorkerSettings, "retry_delay")  # arq never read it


class TestFunctionSetting:
    async def test_max_retries_round_trips_and_is_bounded(self, client, admin_user):
        h = auth_headers(admin_user)
        body = {
            "namespace": "default", "name": f"mr_{uuid.uuid4().hex[:8]}",
            "code": "def handler(input, context):\n    return 1\n",
            "input_schema": {"type": "object"}, "output_schema": {"type": "object"},
        }
        r = await client.post("/api/v1/functions", json=body, headers=h)
        assert r.status_code == 201 and r.json()["max_retries"] == 0
        url = f"/api/v1/functions/default/{body['name']}"
        r = await client.put(url, json={"max_retries": 3}, headers=h)
        assert r.status_code == 200 and r.json()["max_retries"] == 3
        r = await client.put(url, json={"max_retries": 99}, headers=h)
        assert r.status_code == 422
