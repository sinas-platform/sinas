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
  recorded leaves the in-progress marker to expire, after which the job runs
  again, as the deferral asked, just later.

What still doesn't work while Redis is full: anything that must write. Jobs
can't start (arq's in-progress PSETEX is refused; arq treats that as "taken
by another worker" and the job stays queued), enqueues fail, and so do the
job's own status writes. Nothing queued is lost, and the worker is still
there when memory is freed or raised.

Run a worker with `python -m app.queue.arq_worker app.queue.worker.WorkerSettings`
(the chart and the compose files do) instead of `python -m arq ...`, whose
CLI always builds arq's own Worker.
"""
import asyncio
import logging
import random
import sys
import time
from typing import Any, Optional

from arq.constants import abort_jobs_ss, in_progress_key_prefix, job_key_prefix, retry_key_prefix
from arq.worker import Worker

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
            # A Retry/re-queue whose new score (ZINCRBY) was refused: the
            # in-progress marker stays until it expires, then the job runs
            # again, as the deferral asked, only later.
            logger.warning(f"Job {job_id}: its deferral could not be recorded; it re-runs once its in-progress marker expires")
            return
        # Take it off the queue without the result: never run a finished job twice.
        await self._dequeue_without_result(job_id, keep_in_progress)
        logger.error(f"Job {job_id} finished, but Redis was full: its arq result was not stored (the job is not re-run)")

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

    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        sys.exit("usage: python -m app.queue.arq_worker app.queue.worker.<WorkerSettings>")
    sys.path.append(os.getcwd())  # as arq's CLI does
    settings_cls = import_string(args[0])
    logging.config.dictConfig(default_log_config(verbose=False))
    create_worker(settings_cls).run()


if __name__ == "__main__":
    main()
