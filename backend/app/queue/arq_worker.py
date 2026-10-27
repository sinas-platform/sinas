"""The arq worker every Sinas queue runs: arq's, kept alive when Redis is full.

Redis runs with maxmemory-policy noeviction, so at maxmemory it refuses
writes ("OOM command not allowed"). arq 0.28 doesn't catch write errors in
its own loop: one refused write in the poll loop ends `Worker.main()`, which
cancels every in-flight job, and the restarted worker dies again on its
first health record (a crash loop for as long as Redis stays full). The
writes in question, all in arq's code, not ours:

- `record_health`: PSETEX of `<queue>:health-check` (first poll, then hourly);
- `run_cron`: `enqueue_job` of a due cron job (the agent worker has one
  every 5 seconds);
- `finish_job` / `finish_failed_job`: the SET of `arq:result:<id>` in the
  transaction that also takes the job off the queue. Refused, the whole
  transaction is dropped: the job stays queued, and runs AGAIN once its
  in-progress marker expires, though it already ran (side effects twice);
- `run_job`'s INCR of `arq:retry:<id>`: the job fails before it starts.

`SinasWorker` overrides exactly those entry points, and only for an
out-of-memory error (`is_redis_oom`); anything else propagates as before:

- the poll loop logs (throttled), backs off 1-5s, and goes on polling;
- a missed health record or cron enqueue is logged and skipped;
- a finished job whose result can't be stored is still taken off the queue:
  the same cleanup without the result SET, outside MULTI (Redis 6.2 refuses
  any command queued in a transaction while full). DEL, ZREM and PEXPIRE
  never grow memory, so a full Redis accepts them. The job's result is lost
  (logged); the job is not re-run. A deferral (`Retry`) that can't be
  recorded releases the job (DEL of its in-progress marker): it stays
  queued and runs again after the backoff;
- a job whose start-of-run INCR is refused releases its in-progress marker
  too, instead of waiting out its TTL.

The jobs themselves must not turn "Redis is full" into "finished": a job
whose own first write (its "running" status) is refused raises Retry via
`defer_job_redis_full` (app/queue/worker.py, agent_jobs.py), and a function
job defers on any refused write (the engine's guard makes a re-run of a
finished execution a no-op).

What still doesn't work while Redis is full: anything that must write. Jobs
mostly can't start (arq's in-progress PSETEX is refused; arq treats that as
"taken by another worker", and that refusal never reaches this class, so
it causes no backoff), enqueues fail, and so do the jobs' own writes.
Queued jobs stay queued, and the worker is still there when memory is
freed or raised.

Run a worker with `python -m app.queue.arq_worker app.queue.worker.WorkerSettings`
(the chart and the compose files do) instead of `python -m arq ...`, whose
CLI always builds arq's own Worker.
"""
import asyncio
import logging
import random
import sys
import time
from typing import Any, NoReturn, Optional

from arq.constants import (
    abort_jobs_ss,
    expires_extra_ms,
    in_progress_key_prefix,
    job_key_prefix,
    retry_key_prefix,
)
from arq.worker import Retry, Worker

from app.core.redis import is_redis_oom, redis_oom_summary

logger = logging.getLogger(__name__)

# One error log per this many seconds while Redis stays full (with a count
# of what was refused meanwhile), not one per poll.
OOM_LOG_INTERVAL_SECONDS = 30.0
# Pause of the poll loop after a refused write: Redis is full, polling at
# QUEUE_POLL_DELAY would only add load. Jittered so replicas spread out.
OOM_BACKOFF_SECONDS = (1.0, 5.0)


class SinasWorker(Worker):
    """arq's Worker, kept alive when Redis refuses writes (see module doc)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._oom_until = 0.0  # monotonic: no polling before this
        self._oom_logged_at = 0.0
        self._oom_suppressed = 0
        self.oom_errors = 0  # refused writes this worker survived

    def _redis_full(self, where: str, exc: BaseException) -> None:
        """Count, log (throttled) and start a backoff."""
        self.oom_errors += 1
        now = time.monotonic()
        self._oom_until = now + random.uniform(*OOM_BACKOFF_SECONDS)
        if now - self._oom_logged_at >= OOM_LOG_INTERVAL_SECONDS:
            more = f" ({self._oom_suppressed} more refused writes since the last log)" if self._oom_suppressed else ""
            logger.error(
                f"Redis is out of memory (maxmemory reached), {self.queue_name} worker: "
                f"{where} failed ({redis_oom_summary(exc)}){more}. The worker keeps running; queued jobs wait "
                "until Redis has memory again (raise its maxmemory)."
            )
            self._oom_logged_at = now
            self._oom_suppressed = 0
        else:
            self._oom_suppressed += 1

    async def _poll_iteration(self) -> None:
        backoff = self._oom_until - time.monotonic()
        if backoff > 0:
            await asyncio.sleep(backoff)
        try:
            await super()._poll_iteration()
        except Exception as e:
            # Also what a finished job task re-raises here (`t.result()`),
            # e.g. run_job's refused INCR of arq:retry.
            if not is_redis_oom(e):
                raise
            self._redis_full("polling", e)

    async def record_health(self) -> None:
        try:
            await super().record_health()
        except Exception as e:
            if not is_redis_oom(e):
                raise
            self._redis_full("health check", e)
            # arq stamps the time before the write: without this the next
            # try would come a whole health_check_interval (1h) later.
            self._last_health_check = 0

    async def run_cron(self, n: Any, delay: float, num_windows: int = 2) -> None:
        # arq advances each due job's next_run before enqueuing: a refused
        # enqueue skips that one run; the next one is tried as usual.
        try:
            await super().run_cron(n, delay, num_windows)
        except Exception as e:
            if not is_redis_oom(e):
                raise
            self._redis_full("cron enqueue", e)

    async def finish_job(
        self,
        job_id: str,
        finish: bool,
        result_data: Optional[bytes],
        result_timeout_s: Optional[float],
        keep_result_forever: bool,
        incr_score: Optional[int],
        keep_in_progress: Optional[float],
    ) -> None:
        try:
            await super().finish_job(
                job_id, finish, result_data, result_timeout_s, keep_result_forever,
                incr_score, keep_in_progress,
            )
            return
        except Exception as e:
            if not is_redis_oom(e):
                raise
            self._redis_full(f"finishing job {job_id}", e)
        if not finish:
            # A deferral (Retry) whose new score (ZINCRBY) was refused. Let
            # the job go instead (DEL is never refused): it is still queued,
            # at its old score, and runs again once the poll loop's backoff
            # ends: sooner than the deferral asked, never lost.
            if keep_in_progress is None:
                await self.pool.delete(in_progress_key_prefix + job_id)
            logger.warning(f"Job {job_id}: its deferral could not be recorded (Redis full); it stays queued")
            return
        # Take it off the queue without the result: never run a finished job twice.
        await self._dequeue_without_result(job_id, keep_in_progress)
        logger.error(f"Job {job_id} finished, but Redis was full: its arq result was not stored (the job is not re-run)")

    async def run_job(self, job_id: str, score: int) -> None:
        try:
            await super().run_job(job_id, score)
        except Exception as e:
            # Only what arq writes before the job runs can get here (its
            # start-of-run MULTI: INCR arq:retry); refusals in finish_job
            # and finish_failed_job are handled there. The job never ran:
            # drop its in-progress marker so it is picked up again once the
            # backoff ends, instead of waiting out the marker's TTL (the
            # longest job timeout).
            if not is_redis_oom(e):
                raise
            self._redis_full(f"starting job {job_id}", e)
            await self.pool.delete(in_progress_key_prefix + job_id)

    async def finish_failed_job(self, job_id: str, result_data: Optional[bytes]) -> None:
        try:
            await super().finish_failed_job(job_id, result_data)
            return
        except Exception as e:
            if not is_redis_oom(e):
                raise
            self._redis_full(f"finishing failed job {job_id}", e)
        await self._dequeue_without_result(job_id, None)
        logger.error(f"Job {job_id} failed, and Redis was full: its arq result was not stored (the job is not re-run)")


    async def _dequeue_without_result(self, job_id: str, keep_in_progress: Optional[float]) -> None:
        """What finish_job / finish_failed_job do for a finished job, minus
        the result SET: off the queue, its keys gone (a cron job keeps its
        in-progress marker for `keep_in_progress`, as arq does). None of
        these grow memory, so a full Redis accepts them, but only outside
        MULTI: Redis 6.2 refuses every command queued in a transaction while
        it is over maxmemory. Not atomic, so the queue entry goes first: a
        half-done cleanup leaves keys that expire, never a runnable job."""
        in_progress = in_progress_key_prefix + job_id
        async with self.pool.pipeline(transaction=False) as pipe:
            pipe.zrem(self.queue_name, job_id)
            pipe.zrem(abort_jobs_ss, job_id)
            if keep_in_progress is None:
                pipe.delete(in_progress, retry_key_prefix + job_id, job_key_prefix + job_id)
            else:
                pipe.pexpire(in_progress, int(keep_in_progress * 1000))
                pipe.delete(retry_key_prefix + job_id, job_key_prefix + job_id)
            await pipe.execute()


class RedisFullBeforeStart(Exception):
    """Raised by job code whose first write was refused (Redis full) before
    it did anything; the job's entry point defers it (defer_job_redis_full)."""


# Delay before a job that found Redis full before it started runs again.
REDIS_FULL_DEFER_SECONDS = (5.0, 15.0)


async def defer_job_redis_full(redis: Any, job_id: str, what: str, *, reset_tries: bool = False) -> NoReturn:
    """Defer a job that could not start because Redis is full: raise arq's
    Retry, so it stays queued instead of being finished as failed (and, on
    a full Redis, dropped). Call it only before the job did anything that
    must not happen twice.

    The job record's expiry is pushed out first (PEXPIRE is never refused),
    as every deferral does (worker._keep_job_key_alive). `reset_tries`: for
    a queue with max_tries=1 (agents, pipelines), drop the arq:retry counter
    (DEL is never refused) so the re-run isn't refused as "max retries
    exceeded"; this attempt never started."""
    defer = random.uniform(*REDIS_FULL_DEFER_SECONDS)
    try:
        await redis.pexpire(job_key_prefix + job_id, int(defer * 1000) + expires_extra_ms)
        if reset_tries:
            await redis.delete(retry_key_prefix + job_id)
    except Exception:
        logger.warning(f"{what}: could not prepare its deferral", exc_info=True)
    logger.warning(f"{what}: Redis is full, so it did not start; retrying in {defer:.0f}s")
    raise Retry(defer=defer)


def create_worker(settings_cls: Any, **kwargs: Any) -> SinasWorker:
    """arq's create_worker, building a SinasWorker."""
    from arq.worker import get_kwargs

    return SinasWorker(**{**get_kwargs(settings_cls), **kwargs})


def main(argv: Optional[list[str]] = None) -> None:
    """`python -m app.queue.arq_worker <dotted WorkerSettings>`: what
    `python -m arq <settings>` does (arq's logging config, then run until
    signalled), with a SinasWorker."""
    import logging.config
    import os

    from arq.logs import default_log_config
    from arq.utils import import_string

    import argparse

    from arq.worker import check_health

    parser = argparse.ArgumentParser(prog="python -m app.queue.arq_worker")
    parser.add_argument("worker_settings", help="e.g. app.queue.worker.WorkerSettings")
    parser.add_argument("--burst", action="store_true", help="exit once no jobs are queued")
    parser.add_argument("--check", action="store_true", help="run arq's health check and exit")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    sys.path.append(os.getcwd())  # as arq's CLI does
    settings_cls = import_string(args.worker_settings)
    logging.config.dictConfig(default_log_config(verbose=args.verbose))
    if args.check:
        sys.exit(check_health(settings_cls))
    create_worker(settings_cls, **({"burst": True} if args.burst else {})).run()


if __name__ == "__main__":
    main()
