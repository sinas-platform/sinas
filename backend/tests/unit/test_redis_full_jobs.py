"""Jobs on a full Redis are deferred, never finished as failed and dropped.

On a full Redis (maxmemory, noeviction) a job's own first write (its
"running" status) is refused. Finished as a failure, its result SET is
refused too, and SinasWorker takes it off the queue: a function execution
then stays PENDING for good, an agent turn or a pipeline run vanishes. Now:

- a function job defers on any refused write (Retry, not an attempt); the
  engine returns the stored outcome when an execution that finished comes
  again, so a re-run never repeats a run;
- a refused write before dispatch inside the engine (e.g. the admission
  gate) is an infrastructure failure: retried, then dead-lettered;
- agent, pipeline and background jobs defer when their first write is
  refused (agents and pipelines run with max_tries=1: the arq:retry counter
  is reset so the re-run isn't refused);
- a database blip while checking whether the execution already finished is
  retried like any other before the run (regression: it used to fail the
  execution for good);
- an execution cancelled while queued is reported as cancelled;
- a refused command's error text names the command, never its arguments
  (they carry the job's user token).
"""

import asyncio
import json
import uuid

import pytest
from arq import func
from arq.connections import create_pool
from arq.constants import job_key_prefix, retry_key_prefix
from arq.worker import Retry
from redis.exceptions import OutOfMemoryError
from sqlalchemy.exc import OperationalError

from app.core.config import settings
from app.core.redis import get_redis, get_redis_settings, is_redis_oom
from app.models.execution import ExecutionStatus
from app.queue import arq_worker
from app.services.execution_engine import executor
from app.services.executor.base import ExecutionResult, ExecutorUnavailable
from app.services.queue_service import (
    DLQ_DROPPED_KEY,
    DLQ_KEY,
    JOB_DONE_CHANNEL_PREFIX,
    JOB_STATUS_PREFIX,
    push_dead_letter,
    trim_dead_letter_queue,
)
from tests.unit.test_function_queue_retry import (  # noqa: F401 — fixtures
    _dlq,
    _FakeRedis,
    _function,
    _kwargs,
    _pending_row,
    _run_until_final,
    sandbox,
    use_test_db,
)

_OOM = "command not allowed when used memory > 'maxmemory'."


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(arq_worker, "OOM_BACKOFF_SECONDS", (0.05, 0.1))
    monkeypatch.setattr(arq_worker, "REDIS_FULL_DEFER_SECONDS", (0.2, 0.4))

    async def not_cancelled(execution_id):
        return False

    monkeypatch.setattr("app.queue.worker._execution_cancelled", not_cancelled)


class _FullRedis(_FakeRedis):
    """A fake Redis that refuses SETs of keys matching `refuse` (as a full
    Redis would) until `refuse` is cleared."""

    def __init__(self, refuse: str = JOB_STATUS_PREFIX):
        super().__init__()
        self.refuse = refuse
        self.deleted: list[str] = []

    async def set(self, key, value, ex=None, nx=False):
        if self.refuse and self.refuse in key:
            raise OutOfMemoryError(_OOM)
        await super().set(key, value, ex=ex)

    async def delete(self, *keys):
        self.deleted.extend(keys)
        await super().delete(*keys)


@pytest.mark.usefixtures("use_test_db")
class TestFunctionJobs:
    async def test_refused_running_status_defers_and_runs_later(self, db, test_user, sandbox):
        from app.queue import worker

        fn = await _function(db, test_user)
        row = await _pending_row(db, test_user, fn)
        fake = sandbox(ExecutionResult.from_wire({"result": 7, "status": "completed"}))
        redis = _FullRedis()
        kwargs = _kwargs(fn, row, test_user)

        with pytest.raises(Retry):
            await worker.execute_function_job({"redis": redis, "job_try": 1}, **kwargs)
        assert fake.calls == 0  # nothing ran
        await db.refresh(row)
        assert row.status == ExecutionStatus.PENDING and row.error is None
        assert _dlq(redis) == [] and redis.published == []
        assert f"pexpire:{job_key_prefix}{row.execution_id}" in redis.store  # record kept alive

        redis.refuse = ""  # memory again
        assert await worker.execute_function_job({"redis": redis, "job_try": 2}, **kwargs) == 7
        assert fake.calls == 1

    async def test_refused_result_after_the_run_defers_and_never_reruns(self, db, test_user, sandbox):
        from app.queue import worker
        from app.services.queue_service import JOB_RESULT_PREFIX

        fn = await _function(db, test_user)
        row = await _pending_row(db, test_user, fn)
        fake = sandbox(ExecutionResult.from_wire({"result": 42, "status": "completed"}))
        redis = _FullRedis(refuse=JOB_RESULT_PREFIX)
        kwargs = _kwargs(fn, row, test_user)

        with pytest.raises(Retry):
            await worker.execute_function_job({"redis": redis, "job_try": 1}, **kwargs)
        await db.refresh(row)
        assert row.status == ExecutionStatus.COMPLETED  # not turned into a failure
        assert fake.calls == 1

        redis.refuse = ""
        assert await worker.execute_function_job({"redis": redis, "job_try": 2}, **kwargs) == 42
        assert fake.calls == 1  # the stored output, not a second run
        assert json.loads(redis.store[f"{JOB_STATUS_PREFIX}{row.execution_id}"])["status"] == "completed"

    async def test_refused_write_before_dispatch_is_an_infrastructure_retry(
        self, db, test_user, sandbox, monkeypatch
    ):
        """E.g. the admission gate's ZADD: retried with backoff, then
        dead-lettered, like an outage, never a permanent function failure."""
        from app.queue import worker

        monkeypatch.setattr(settings, "queue_max_retries", 1)
        fn = await _function(db, test_user)
        row = await _pending_row(db, test_user, fn)

        async def refused(*args, **kwargs):
            raise OutOfMemoryError(_OOM)

        monkeypatch.setattr(executor, "load_function", refused)
        redis = _FakeRedis()
        delays, exc = await _run_until_final(worker, redis, _kwargs(fn, row, test_user))

        assert len(delays) == 1
        assert isinstance(exc, ExecutorUnavailable)
        (entry,) = _dlq(redis)
        assert entry["error_type"] == "infrastructure"

    @pytest.mark.parametrize("fail_on", ["first query", "the execution lookup"])
    async def test_a_database_blip_before_the_run_is_retried(
        self, db, test_user, sandbox, monkeypatch, fail_on
    ):
        """Regression: the finished-execution check ran outside the try that
        maps a lost connection to ExecutorUnavailable, so a blip there failed
        the execution for good (no retry, no DLQ)."""
        from app.queue import worker

        fn = await _function(db, test_user)
        row = await _pending_row(db, test_user, fn)
        fake = sandbox(ExecutionResult.from_wire({"result": 1, "status": "completed"}))
        real_execute = db.execute
        failed = []

        async def flaky_execute(statement, *args, **kwargs):
            hit = not failed and (fail_on == "first query" or "FROM executions" in str(statement))
            if hit:
                failed.append(statement)
                raise OperationalError(str(statement), {}, ConnectionResetError("connection reset"))
            return await real_execute(statement, *args, **kwargs)

        monkeypatch.setattr(db, "execute", flaky_execute)
        delays, exc = await _run_until_final(worker, _FakeRedis(), _kwargs(fn, row, test_user))

        assert failed and len(delays) == 1  # retried once
        assert exc is None and fake.calls == 1
        await db.refresh(row)
        assert row.status == ExecutionStatus.COMPLETED

    async def test_a_cancelled_execution_is_reported_cancelled(self, db, test_user, sandbox):
        from app.queue import worker

        fn = await _function(db, test_user)
        row = await _pending_row(db, test_user, fn)
        row.status = ExecutionStatus.CANCELLED
        await db.commit()
        fake = sandbox(RuntimeError("must not run"))
        redis = _FakeRedis()

        assert await worker.execute_function_job(
            {"redis": redis, "job_try": 1}, **_kwargs(fn, row, test_user)
        ) is None
        assert fake.calls == 0
        status = json.loads(redis.store[f"{JOB_STATUS_PREFIX}{row.execution_id}"])
        assert status["status"] == "cancelled"
        ((channel, payload),) = redis.published
        assert channel == f"{JOB_DONE_CHANNEL_PREFIX}{row.execution_id}"
        assert json.loads(payload)["status"] == "cancelled"
        assert _dlq(redis) == []


@pytest.mark.usefixtures("use_test_db")
class TestThirdReview:
    async def test_a_paused_run_is_not_rerun_after_a_refused_result(self, db, test_user, sandbox):
        """The run pauses for input (row AWAITING_INPUT, committed), then the
        worker's result SET is refused and the job defers. The re-run must
        not start the function again (side effects twice, and an orphaned
        paused run): only resume_execution continues it."""
        from app.queue import worker
        from app.services.queue_service import JOB_RESULT_PREFIX

        fn = await _function(db, test_user)
        row = await _pending_row(db, test_user, fn)
        fake = sandbox(ExecutionResult.from_wire({
            "status": "awaiting_input", "prompt": "Which one?", "container_name": "c-1",
        }))
        redis = _FullRedis(refuse=JOB_RESULT_PREFIX)
        kwargs = _kwargs(fn, row, test_user)

        with pytest.raises(Retry):
            await worker.execute_function_job({"redis": redis, "job_try": 1}, **kwargs)
        assert fake.calls == 1
        await db.refresh(row)
        assert row.status == ExecutionStatus.AWAITING_INPUT

        redis.refuse = ""
        result = await worker.execute_function_job({"redis": redis, "job_try": 2}, **kwargs)
        assert fake.calls == 1  # not run again
        assert result == {"status": "awaiting_input", "execution_id": row.execution_id, "prompt": "Which one?"}
        await db.refresh(row)
        assert row.status == ExecutionStatus.AWAITING_INPUT

    async def test_deferrals_never_use_up_arqs_tries(self, db, test_user, sandbox):
        from app.queue import worker

        fn = await _function(db, test_user)
        row = await _pending_row(db, test_user, fn)
        sandbox(ExecutionResult.from_wire({"result": 1, "status": "completed"}))
        redis = _FullRedis()
        with pytest.raises(Retry):
            await worker.execute_function_job(
                {"redis": redis, "job_try": 500, "job_id": row.execution_id}, **_kwargs(fn, row, test_user)
            )
        assert retry_key_prefix + row.execution_id in redis.deleted

    async def test_the_max_wait_counts_from_the_first_refusal(self, db, test_user, sandbox, monkeypatch):
        """A job queued hours ago (scheduled ahead, or waiting for a pool
        slot) that hits its first refused write is deferred, not failed;
        one that has kept finding Redis full past the max wait gives up."""
        from datetime import datetime, timedelta, timezone

        from app.queue import arq_worker, worker
        from app.queue.arq_worker import RedisFullTooLong

        monkeypatch.setattr(settings, "queue_redis_full_max_wait_seconds", 3600)
        monkeypatch.setattr(arq_worker, "_redis_full_since", {})
        fn = await _function(db, test_user)
        row = await _pending_row(db, test_user, fn)
        fake = sandbox(ExecutionResult.from_wire({"result": 1, "status": "completed"}))
        ctx = {
            "redis": _FullRedis(), "job_try": 9, "job_id": row.execution_id,
            "enqueue_time": datetime.now(timezone.utc) - timedelta(hours=2),
        }

        with pytest.raises(Retry):  # first refusal: defer, whatever the job's age
            await worker.execute_function_job(ctx, **_kwargs(fn, row, test_user))
        await db.refresh(row)
        assert row.status == ExecutionStatus.PENDING

        # ...and some 61 minutes of refusals later (deferred all along):
        first, last = arq_worker._redis_full_since[row.execution_id]
        arq_worker._redis_full_since[row.execution_id] = (first - 3660, last)
        with pytest.raises(RedisFullTooLong):
            await worker.execute_function_job(ctx, **_kwargs(fn, row, test_user))
        assert fake.calls == 0
        await db.refresh(row)
        assert row.status == ExecutionStatus.FAILED and "Redis was full" in row.error

    async def test_a_new_episode_restarts_the_clock(self, monkeypatch):
        """Refusals separated by a gap (Redis had memory in between) are
        separate episodes: an old episode doesn't count against a new one."""
        from app.queue import arq_worker

        monkeypatch.setattr(arq_worker, "_redis_full_since", {})
        import time

        now = time.monotonic()
        gap = arq_worker.REDIS_FULL_EPISODE_GAP_SECONDS
        arq_worker._redis_full_since["j"] = (now - 3 * gap, now - 2 * gap)  # an old episode
        assert arq_worker._redis_full_for("j") < 1
        # within an episode the clock keeps running
        arq_worker._redis_full_since["k"] = (now - 900, now - 10)
        assert arq_worker._redis_full_for("k") >= 900

    async def test_an_agent_turn_gives_up_with_a_recorded_error(self, monkeypatch):
        from datetime import datetime, timedelta, timezone

        from app.queue import agent_jobs
        from app.queue.arq_worker import RedisFullTooLong

        recorded = {}

        async def persist(chat_id, error):
            recorded["chat"] = (chat_id, str(error))

        async def terminate(execution_id, status, **kw):
            recorded["execution"] = (execution_id, status)

        async def report(delegation_id, tool_call_id, content, user_token=None):
            recorded["parent"] = (delegation_id, tool_call_id, json.loads(content))

        monkeypatch.setattr(agent_jobs, "_persist_turn_error", persist)
        monkeypatch.setattr(agent_jobs, "_terminate_execution_row", terminate)
        monkeypatch.setattr("app.services.delegation.on_child_complete", report)
        ctx = {"job_id": "j", "enqueue_time": datetime.now(timezone.utc) - timedelta(hours=2)}

        # Queued 2h ago, first refusal: deferred, nothing recorded.
        monkeypatch.setattr(arq_worker, "_redis_full_since", {})
        with pytest.raises(Retry):
            await agent_jobs._mark_running(ctx, _FullRedis(), "j", {}, chat_id="chat-1", user_token="t")
        assert recorded == {}

        # Refused for over an hour: the turn ends, on record.
        first, last = arq_worker._redis_full_since["j"]
        arq_worker._redis_full_since["j"] = (first - 3700, last)
        with pytest.raises(RedisFullTooLong):
            await agent_jobs._mark_running(
                ctx, _FullRedis(), "j", {}, chat_id="chat-1", user_token="t",
                execution_id="ex-1", parent_delegation_id="pd-1", parent_tool_call_id="tc-1",
            )
        assert recorded["chat"][0] == "chat-1" and "Redis was full" in recorded["chat"][1]
        assert recorded["execution"] == ("ex-1", ExecutionStatus.FAILED)
        assert recorded["parent"][:2] == ("pd-1", "tc-1")
        assert recorded["parent"][2]["chat_id"] == "chat-1"


async def test_a_refired_pipeline_enqueues_each_run_once():
    """A fire job re-run after Redis was full mid fan-out: the runs it had
    already enqueued are not enqueued again (ids derive from the fire)."""
    from app.services.queue_service import PIPELINE_QUEUE, queue_service

    redis = await get_redis()
    first = await queue_service.enqueue_pipeline_run(
        "p", {}, "manual", None, "u1", dedup_key="fire-1:u1"
    )
    again = await queue_service.enqueue_pipeline_run(
        "p", {}, "manual", None, "u1", dedup_key="fire-1:u1"
    )
    other = await queue_service.enqueue_pipeline_run(
        "p", {}, "manual", None, "u2", dedup_key="fire-1:u2"
    )
    assert first == again != other
    assert await redis.zcard(PIPELINE_QUEUE) == 2


async def test_the_fire_job_defers_on_a_full_redis(monkeypatch):
    from app.queue import worker
    from app.services import pipeline_runner

    seen = {}

    async def refused(*args, **kwargs):
        seen.update(kwargs)
        raise OutOfMemoryError(_OOM)

    monkeypatch.setattr(pipeline_runner, "fire_pipeline", refused)
    ctx = {"redis": _FullRedis(), "job_id": "fire-9"}
    with pytest.raises(Retry):
        await worker.execute_pipeline_fire_job(ctx, job_id="fire-9", namespace="n", name="p")
    assert seen["fire_id"] == "fire-9"
    assert retry_key_prefix + "fire-9" in ctx["redis"].deleted


def test_error_text_keeps_the_key_but_not_secrets_or_values():
    from redis.exceptions import ResponseError

    from app.core.redis import _annotate_without_arguments

    def annotate(*command):
        exc = ResponseError("WRONGTYPE Operation against a key holding the wrong kind of value")
        _annotate_without_arguments(None, exc, 1, command)
        return str(exc)

    assert annotate("SET", "chat:tool-grant:abc", "SECRET-VALUE").startswith(
        "Command # 1 (SET chat:tool-grant:abc) of pipeline caused error: WRONGTYPE"
    )
    assert "SECRET" not in annotate(b"SET", b"arq:job:1", b"\x80SECRET-pickle")
    assert "(SET sso_code:…)" in annotate("SET", "sso_code:ONE-TIME-CODE", "{}")
    assert "ONE-TIME" not in annotate("SET", "sso_code:ONE-TIME-CODE", "{}")
    assert "(SET connector_oauth_state:…)" in annotate("SET", "connector_oauth_state:STATE", "x")
    assert "(INCR sinas:ratelimit:…)" in annotate("INCR", "sinas:ratelimit:login:email:a@b.c")
    assert "(EVAL)" in annotate("EVAL", "return redis.call('GET', KEYS[1])", 1, "k")
    assert "(MULTI)" in annotate("MULTI")
    # An allowlist: a key family nobody listed shows only its prefix.
    assert "(SET newthing:…)" in annotate("SET", "newthing:SECRET-TOKEN", "v")
    assert "(SET sinas:newthing:…)" in annotate("SET", "sinas:newthing:SECRET-TOKEN", "v")
    assert "(GET …)" in annotate("GET", "SECRET-BARE-KEY")
    assert "SECRET" not in annotate("SET", "newthing:a:SECRET", "v")
    for key in ("arq:in-progress:j1", "sinas:job:status:e1", "sinas:stream:c1", "usage:i:2026-10:total",
                "sinas:queue:dlq", "chat:loop-lock:c1", "sinas:pipeline:lock:p:u"):
        assert f"(SET {key})" in annotate("SET", key, "v"), key


class TestOtherJobs:
    async def test_agent_job_defers_and_resets_its_tries(self):
        from app.queue.agent_jobs import _mark_running

        redis = _FullRedis()
        with pytest.raises(Retry):
            await _mark_running({"job_id": "j1"}, redis, "j1", {"chat_id": "c"}, chat_id="c", user_token="t")
        assert retry_key_prefix + "j1" in redis.deleted  # max_tries=1 queue: re-run allowed
        assert f"pexpire:{job_key_prefix}j1" in redis.store

    async def test_pipeline_run_defers_until_its_record_exists(self, monkeypatch):
        from app.queue import worker
        from app.services import pipeline_runner

        async def refused(*args, **kwargs):
            raise OutOfMemoryError(_OOM)

        async def token(user_id):
            return "tok"

        monkeypatch.setattr(pipeline_runner, "run_pipeline", refused)
        monkeypatch.setattr(pipeline_runner, "mint_run_token", token)
        recorded = {"value": False}

        async def run_recorded(run_id):
            return recorded["value"]

        monkeypatch.setattr(worker, "_pipeline_run_recorded", run_recorded)
        kwargs = {"job_id": "pj", "pipeline_id": "p", "user_id": "u", "run_id": "r"}
        ctx = {"redis": _FullRedis(), "job_id": "pj"}

        with pytest.raises(Retry):
            await worker.execute_pipeline_run_job(ctx, **kwargs)
        assert retry_key_prefix + "pj" in ctx["redis"].deleted

        recorded["value"] = True  # the run started: its record carries the failure
        with pytest.raises(OutOfMemoryError):
            await worker.execute_pipeline_run_job(ctx, **kwargs)

    async def test_background_job_defers_before_running(self, monkeypatch):
        from app.queue import worker
        from app.services import chat_wakeups

        redis = _FullRedis(refuse=chat_wakeups.BG_JOB_PREFIX)

        async def get_full():
            return redis

        monkeypatch.setattr("app.core.redis.get_redis", get_full)
        ran = []
        monkeypatch.setattr("app.services.code_execution.run_code", lambda *a, **k: ran.append(1), raising=False)
        with pytest.raises(Retry):
            await worker.execute_background_code_job(
                {"redis": redis, "job_id": "bg1"},
                job_id="bg1", chat_id=str(uuid.uuid4()), user_id=str(uuid.uuid4()), code="print(1)",
            )
        assert ran == []


async def test_dlq_trim_counts_each_drop_once_across_processes(monkeypatch):
    redis = await get_redis()
    monkeypatch.setattr(settings, "queue_dlq_max_entries", 0)
    for i in range(60):
        await push_dead_letter(redis, json.dumps({"job_id": i}))
    monkeypatch.setattr(settings, "queue_dlq_max_entries", 10)

    dropped = await asyncio.gather(*(trim_dead_letter_queue(redis) for _ in range(9)))

    assert sum(dropped) == 50 and sorted(dropped)[-1] == 50  # one trim did it all
    assert await redis.llen(DLQ_KEY) == 10
    assert int(await redis.get(DLQ_DROPPED_KEY)) == 50


class TestRefusedCommandErrorsHaveNoArguments:
    async def test_arq_enqueue_on_a_full_redis(self, fill_redis):
        pool = await create_pool(get_redis_settings())
        try:
            fill_redis.fill()
            with pytest.raises(Exception) as exc:
                await pool.enqueue_job(
                    "execute_agent_message_job", user_token="SECRET-TOKEN-abc123",
                    _queue_name=f"sinas:test:q:{uuid.uuid4().hex[:6]}",
                )
        finally:
            await pool.aclose()
        assert is_redis_oom(exc.value)
        assert "SECRET" not in str(exc.value) and "SECRET" not in repr(exc.value)
        assert "Command # " in str(exc.value)  # still says which command

    async def test_app_client_pipeline_on_a_full_redis(self, fill_redis):
        redis = await get_redis()
        fill_redis.fill()
        with pytest.raises(Exception) as exc:
            async with redis.pipeline(transaction=False) as pipe:
                pipe.set("k", json.dumps({"input_data": "SECRET-INPUT"}))
                await pipe.execute()
        assert is_redis_oom(exc.value) and "SECRET" not in str(exc.value)
        assert "(SET …)" in str(exc.value)  # "k" is no known id-named key


async def _run_worker(worker, seconds):
    try:
        await asyncio.wait_for(worker.main(), seconds)
    except asyncio.TimeoutError:
        pass
    finally:
        for t in worker.tasks.values():
            t.cancel()
        await asyncio.gather(*worker.tasks.values(), return_exceptions=True)


@pytest.mark.parametrize("max_tries", [1000, 1], ids=["function-like", "agent-like (max_tries=1)"])
async def test_no_job_is_lost_on_a_redis_at_its_limit(fill_redis, max_tries):
    """The reviewer's drop loop: 50 jobs whose first act is a status write,
    on a Redis at its limit where a few writes get through now and then.
    Before: some jobs started, their status write was refused, and they were
    finished as failed and dropped. Now every job runs exactly once."""
    from app.queue.arq_worker import SinasWorker, defer_job_redis_full

    queue = f"sinas:test:dropq:{uuid.uuid4().hex[:6]}"
    runs: dict[int, int] = {}

    async def work(ctx, n):
        try:
            await ctx["redis"].set(f"sinas:test:status:{queue}:{n}", "r" * 2000, ex=600)
        except Exception as e:
            if not is_redis_oom(e):
                raise
            await defer_job_redis_full(ctx["redis"], ctx["job_id"], f"job {n}", reset_tries=max_tries == 1)
        runs[n] = runs.get(n, 0) + 1

    pool = await create_pool(get_redis_settings())
    try:
        for i in range(50):
            await pool.enqueue_job("work", i, _queue_name=queue)
        await pool.set(f"sinas:test:pad:{queue}", "x" * 300_000)
        fill_redis.fill(below_used=100_000)
        worker = SinasWorker(
            functions=[func(work, name="work", max_tries=max_tries)], redis_pool=pool,
            queue_name=queue, poll_delay=0.05, max_jobs=50, handle_signals=False,
        )

        async def release_later():
            await asyncio.sleep(4)
            fill_redis.release()

        releaser = asyncio.ensure_future(release_later())
        await _run_worker(worker, 9)
        await releaser
        assert await pool.zcard(queue) == 0
    finally:
        await pool.aclose()
    assert sorted(runs) == list(range(50)), f"lost: {sorted(set(range(50)) - set(runs))}"
    assert set(runs.values()) == {1}
