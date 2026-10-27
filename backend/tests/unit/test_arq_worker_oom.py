"""Queue workers keep running when Redis is full (maxmemory, noeviction).

arq 0.28's own Worker exits on the first write Redis refuses in its loop
(a cron enqueue, the health record, a finished job's result), cancelling
every job in flight; restarted, it dies again on its first health record.
SinasWorker survives those, and a job that finished while Redis was full is
taken off the queue: it doesn't run a second time.

Against the real Redis, filled with CONFIG SET maxmemory 1 (the `fill_redis`
fixture refuses anything but a throwaway local Redis, and restores it).
"""

import asyncio
import uuid

import pytest
from arq import cron, func
from arq.connections import create_pool
from arq.constants import in_progress_key_prefix, job_key_prefix, result_key_prefix
from arq.worker import Worker
from redis.exceptions import ResponseError

from app.core.redis import get_redis_settings
from app.queue import arq_worker
from app.queue.arq_worker import SinasWorker


@pytest.fixture(autouse=True)
def _short_backoff(monkeypatch):
    monkeypatch.setattr(arq_worker, "OOM_BACKOFF_SECONDS", (0.05, 0.1))


@pytest.fixture
async def pool():
    p = await create_pool(get_redis_settings())
    yield p
    await p.aclose()


async def _run_for(worker: Worker, seconds: float) -> str:
    """Run the worker's loop for `seconds`: "running" if it was still going,
    else how it ended."""
    try:
        await asyncio.wait_for(worker.main(), seconds)
    except asyncio.TimeoutError:
        return "running"
    except Exception as e:  # noqa: BLE001 — what the test asserts on
        return f"crashed: {type(e).__name__}"
    finally:
        for t in worker.tasks.values():
            t.cancel()
        await asyncio.gather(*worker.tasks.values(), return_exceptions=True)
    return "returned"


def _queue():
    return f"sinas:test:oomq:{uuid.uuid4().hex[:8]}"


async def _tick(ctx):
    return None


def _worker(cls, pool, queue, functions, cron_jobs=()):
    return cls(
        functions=functions, cron_jobs=list(cron_jobs), redis_pool=pool, queue_name=queue,
        poll_delay=0.05, handle_signals=False,
    )


class TestPollLoop:
    async def test_arq_worker_exits_on_a_refused_cron_enqueue(self, pool, fill_redis):
        """Why SinasWorker exists: arq's own Worker dies (pins arq 0.28)."""
        queue = _queue()
        worker = _worker(Worker, pool, queue, [func(_tick, name="noop")],
                         [cron(_tick, name=f"tick-{queue}", second=set(range(60)))])
        fill_redis.fill()
        assert (await _run_for(worker, 4)).startswith("crashed")

    async def test_sinas_worker_survives_cron_and_health_writes(self, pool, fill_redis):
        queue = _queue()
        worker = _worker(SinasWorker, pool, queue, [func(_tick, name="noop")],
                         [cron(_tick, name=f"tick-{queue}", second=set(range(60)))])
        fill_redis.fill()  # also refuses the health record of the first poll
        assert await _run_for(worker, 3.5) == "running"
        assert worker.oom_errors >= 2  # the health record, at least one cron enqueue

    async def test_starts_jobs_again_once_redis_has_memory(self, pool, fill_redis):
        """A worker (re)started on a full Redis waits; queued jobs then run."""
        queue = _queue()
        ran: list[int] = []

        async def job(ctx, n):
            ran.append(n)

        await pool.enqueue_job("job", 7, _queue_name=queue)
        worker = _worker(SinasWorker, pool, queue, [func(job, name="job")])
        fill_redis.fill()

        async def release_later():
            await asyncio.sleep(1.5)
            assert ran == []  # nothing could start while full
            fill_redis.release()

        releaser = asyncio.ensure_future(release_later())
        assert await _run_for(worker, 4) == "running"
        await releaser
        assert ran == [7]
        # the refused health record was written once Redis had memory again
        assert await pool.exists(worker.health_check_key)


class TestFinishedJobs:
    async def test_a_job_finished_while_full_is_not_run_again(self, pool, fill_redis):
        queue = _queue()
        started = asyncio.Event()
        runs: list[str] = []

        async def job(ctx):
            runs.append(ctx["job_id"])
            started.set()
            await asyncio.sleep(0.5)  # Redis fills up meanwhile
            return "done"

        enqueued = await pool.enqueue_job("job", _queue_name=queue)
        job_id = enqueued.job_id
        worker = _worker(SinasWorker, pool, queue, [func(job, name="job")])

        async def fill_once_started():
            await started.wait()
            fill_redis.fill()
            await asyncio.sleep(1.5)  # the job finishes in here
            # Still full: the job is off the queue though its result was refused.
            assert await pool.zscore(queue, job_id) is None
            assert not await pool.exists(
                job_key_prefix + job_id, in_progress_key_prefix + job_id, result_key_prefix + job_id
            )
            fill_redis.release()

        filler = asyncio.ensure_future(fill_once_started())
        # Long enough for a re-run, had the job stayed queued.
        assert await _run_for(worker, 4) == "running"
        await filler
        assert runs == [job_id]
        assert worker.oom_errors >= 1

    async def test_a_failed_job_finished_while_full_is_not_run_again(self, pool, fill_redis):
        queue = _queue()
        runs: list[int] = []

        async def job(ctx):
            runs.append(1)
            fill_redis.fill()
            raise RuntimeError("boom")

        enqueued = await pool.enqueue_job("job", _queue_name=queue)
        worker = _worker(SinasWorker, pool, queue, [func(job, name="job")])

        async def release_later():
            await asyncio.sleep(1.5)
            assert await pool.zscore(queue, enqueued.job_id) is None
            fill_redis.release()

        releaser = asyncio.ensure_future(release_later())
        assert await _run_for(worker, 3) == "running"
        await releaser
        assert runs == [1]


class TestOnlyOutOfMemory:
    async def test_other_redis_errors_still_propagate(self, monkeypatch):
        async def wrongtype(self):
            raise ResponseError("WRONGTYPE Operation against a key holding the wrong kind of value")

        monkeypatch.setattr(Worker, "record_health", wrongtype)
        worker = SinasWorker(functions=[func(_tick, name="noop")], redis_pool=object(),
                             queue_name=_queue(), handle_signals=False)
        with pytest.raises(ResponseError, match="WRONGTYPE"):
            await worker.record_health()
        assert worker.oom_errors == 0

    async def test_logs_are_throttled(self, monkeypatch, caplog):
        from redis.exceptions import OutOfMemoryError

        async def oom(self):
            raise OutOfMemoryError("command not allowed when used memory > 'maxmemory'.")

        monkeypatch.setattr(Worker, "record_health", oom)
        worker = SinasWorker(functions=[func(_tick, name="noop")], redis_pool=object(),
                             queue_name=_queue(), handle_signals=False)
        with caplog.at_level("ERROR", logger="app.queue.arq_worker"):
            for _ in range(5):
                await worker.record_health()
        assert worker.oom_errors == 5
        assert len([r for r in caplog.records if "out of memory" in r.getMessage()]) == 1


def test_logs_name_the_command_never_its_arguments():
    """A refused finish quotes the pickled job (with its user token) in
    redis-py's message; the log gets the command name only."""
    from redis.exceptions import OutOfMemoryError

    from app.core.redis import redis_oom_summary

    exc = OutOfMemoryError(
        "Command # 1 (SET arq:result:abc \\x80\\x04user_token=eyJsecret PX 3600000) of pipeline "
        "caused error: (\"command not allowed when used memory > 'maxmemory'.\",)"
    )
    summary = redis_oom_summary(exc)
    assert summary.startswith("SET refused") and "eyJ" not in summary and "arq:result" not in summary
    assert "refused" in redis_oom_summary(OutOfMemoryError("command not allowed"))


def test_every_deployment_runs_the_sinas_worker():
    """The chart, compose and the all-in-one profile start SinasWorker, not
    arq's CLI (which always builds arq's own Worker)."""
    from pathlib import Path

    from app.core import allinone

    root = Path(__file__).resolve().parents[3]
    for path in ("docker-compose.yml", "charts/sinas/templates/workers.yaml"):
        text = (root / path).read_text()
        assert "-m arq" not in text and '"arq"' not in text, path
        assert text.count("app.queue.arq_worker") == 4, path
    assert allinone.create_worker is arq_worker.create_worker
