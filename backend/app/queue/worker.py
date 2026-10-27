"""arq worker definitions for function and agent execution."""
import asyncio
import json
import logging
import random
import time
import uuid
from typing import Any, Optional

from arq import func
from arq.constants import expires_extra_ms, job_key_prefix
from arq.cron import cron
from arq.worker import Retry

from app.core.config import settings
from app.core.redis import create_redis_client, get_redis_settings
from app.core.telemetry import traced_job
from app.schemas.spec.function import MAX_FUNCTION_RETRIES
from app.services.shared_admission import SharedPoolSaturated
from app.services.queue_service import (
    JOB_FAILURES_PREFIX,
    JOB_RESULT_PREFIX,
    JOB_STATUS_PREFIX,
    JOB_TTL,
    JOB_DONE_CHANNEL_PREFIX,
    push_dead_letter,
    startup_redis_checks,
)

logger = logging.getLogger(__name__)

WORKER_HEARTBEAT_PREFIX = "sinas:worker:active:"
WORKER_HEARTBEAT_TTL = 30  # seconds — key auto-expires if worker dies
WORKER_HEARTBEAT_INTERVAL = 10  # seconds — refresh frequency


async def _heartbeat_loop(redis, worker_id: str, data: dict) -> None:
    """Background task that refreshes the worker heartbeat key."""
    key = f"{WORKER_HEARTBEAT_PREFIX}{worker_id}"
    while True:
        try:
            data["last_heartbeat"] = time.time()
            await redis.set(key, json.dumps(data), ex=WORKER_HEARTBEAT_TTL)
        except Exception:
            pass
        await asyncio.sleep(WORKER_HEARTBEAT_INTERVAL)


# Failure retries back off exponentially from QUEUE_RETRY_DELAY, up to this.
RETRY_BACKOFF_CAP_SECONDS = 300


def _retry_backoff(attempt: int) -> float:
    """Delay before re-running a job after its `attempt`-th failure:
    QUEUE_RETRY_DELAY doubled per failure, capped, plus up to 20% jitter so
    jobs that failed together (a worker outage) don't retry in lockstep."""
    base = max(1, settings.queue_retry_delay)
    delay = min(base * 2 ** (attempt - 1), RETRY_BACKOFF_CAP_SECONDS)
    return delay + random.uniform(0, delay * 0.2)


async def _keep_job_key_alive(redis, job_id: str, defer_seconds: float) -> Optional[bool]:
    """Push out the expiry of arq's job record before deferring the job.

    arq sets the TTL of `arq:job:<id>` once, at enqueue (expected start +
    1 day), and a `Retry` leaves it alone. A job deferred again and again
    (shared-pool saturation with a long or unlimited window) outlives it, and
    its next run is dropped as "expired" before this code runs: no failure
    recorded, no dead letter, the execution PENDING forever. So on every
    deferral the record gets what arq gives a fresh enqueue: the delay to
    the next run plus 1 day.

    True: extended. False: the record is already gone, so the re-run would
    be dropped (the caller dead-letters the job instead). None: Redis error;
    best effort, the caller defers anyway.
    """
    try:
        return bool(
            await redis.pexpire(
                f"{job_key_prefix}{job_id}", int(defer_seconds * 1000) + expires_extra_ms
            )
        )
    except Exception:
        logger.warning(f"Could not extend the queue record of job {job_id}", exc_info=True)
        return None


class JobRecordLost(RuntimeError):
    """A deferral found arq's record of the job gone; the job was
    dead-lettered instead of deferred (a re-run would never start)."""


async def _execution_cancelled(execution_id: str) -> bool:
    """Was the execution cancelled (queue API, batch cancel) while its job
    waited for a retry or a pool slot? Best effort: on error, run it."""
    try:
        from sqlalchemy import select as _select

        from app.core.database import AsyncSessionLocal
        from app.models.execution import Execution, ExecutionStatus

        async with AsyncSessionLocal() as db:
            status = (
                await db.execute(
                    _select(Execution.status).where(Execution.execution_id == execution_id)
                )
            ).scalar_one_or_none()
        return status == ExecutionStatus.CANCELLED
    except Exception:
        logger.warning(f"Could not check cancellation of execution {execution_id}", exc_info=True)
        return False


async def execute_function_job(ctx: dict, **kwargs: Any) -> Any:
    """
    Execute a function in the worker process.

    Called by arq when a function job is dequeued.
    Delegates to the existing executor.execute_function().

    Retry policy (issue #135). arq re-runs a job only when it raises `Retry`,
    so every re-run here is explicit:

    - shared pool saturated: deferred (backpressure, not failure) until
      QUEUE_SATURATION_TIMEOUT_SECONDS; never counts as a failed attempt;
    - the infrastructure could not start the code (`ExecutorUnavailable`):
      retried with backoff up to QUEUE_MAX_RETRIES times;
    - the function's own code failed: final, unless the function opts in
      with `max_retries` (it may have side effects);
    - deterministic failures (input validation, function missing): final.

    Failed attempts are counted in Redis (JOB_FAILURES_PREFIX), per kind
    (infrastructure / function, each against its own budget), not with
    arq's job_try, which deferrals advance too. A retryable failure that runs
    out of attempts lands in the dead-letter queue, from which an admin can
    requeue it (POST /queue/dlq/{job_id}/retry).
    """
    from redis.asyncio import Redis

    from app.services.execution_engine import (
        INFRASTRUCTURE,
        ExecutionRetryScheduled,
        dead_letter_note,
        executor,
    )
    from app.services.executor.base import ExecutorUnavailable

    job_id = kwargs["job_id"]
    function_namespace = kwargs["function_namespace"]
    function_name = kwargs["function_name"]
    input_data = kwargs["input_data"]
    execution_id = kwargs["execution_id"]
    trigger_type = kwargs["trigger_type"]
    trigger_id = kwargs["trigger_id"]
    user_id = kwargs["user_id"]
    chat_id = kwargs.get("chat_id")
    callback_url = kwargs.get("callback_url")
    depth = kwargs.get("depth", 0)

    # The enqueuing request's attribution context, for the function's access
    # token (this job's own task: nothing to reset).
    from app.core.auth import bind_credential_context

    bind_credential_context(kwargs.get("credential_context"))

    redis: Redis = ctx.get("redis") or create_redis_client()

    # Restore trace context from the enqueue side
    from opentelemetry import context as otel_context
    from opentelemetry import trace as otel_trace

    from app.core.telemetry import extract_trace_context, get_tracer, record_error
    parent_ctx = extract_trace_context(kwargs.get("trace_context", {}))
    _tracer = get_tracer()

    job_try = ctx.get("job_try", 1)
    failures_key = f"{JOB_FAILURES_PREFIX}{job_id}"
    # {"infrastructure": n, "function": m}: earlier failed runs, per kind
    try:
        failures = {k: int(v) for k, v in json.loads(await redis.get(failures_key) or "{}").items()}
    except (TypeError, ValueError, AttributeError):
        failures = {}
    failed_attempts = sum(failures.values())

    logger.info(
        f"Worker executing function {function_namespace}/{function_name} "
        f"(job={job_id}, execution={execution_id}, try={job_try}, failed={failed_attempts})"
    )

    # Read fields from initial status to preserve across updates
    enqueued_at = None
    trigger_type_val = None
    raw = await redis.get(f"{JOB_STATUS_PREFIX}{job_id}")
    if raw:
        try:
            initial = json.loads(raw)
            enqueued_at = initial.get("enqueued_at")
            trigger_type_val = initial.get("trigger_type")
        except (json.JSONDecodeError, TypeError):
            pass

    # Common fields preserved across status updates
    fn_label = f"{function_namespace}/{function_name}"
    base_fields = {
        "execution_id": execution_id,
        "queue": "functions",
        "function": fn_label,
        "trigger_type": trigger_type_val,
        "enqueued_at": enqueued_at,
    }

    # A re-run (after a deferral or a failed attempt) of an execution that
    # was cancelled in the meantime: drop it.
    if job_try > 1 and await _execution_cancelled(execution_id):
        logger.info(f"Function job {job_id} was cancelled while waiting; not re-running it")
        await redis.set(
            f"{JOB_STATUS_PREFIX}{job_id}",
            json.dumps({**base_fields, "status": "cancelled"}),
            ex=JOB_TTL,
        )
        await redis.delete(failures_key)
        return None

    # Update status to running
    running = {**base_fields, "status": "running"}
    if failed_attempts:
        running["attempt"] = failed_attempts + 1
    await redis.set(f"{JOB_STATUS_PREFIX}{job_id}", json.dumps(running), ex=JOB_TTL)

    async def schedule_retry(error: str, kind: str, attempt: int, max_attempts: int) -> None:
        """Count the failed attempt and re-run the job after a backoff. The
        re-run never hinges on these writes: Redis may be what just failed
        (a lost count only means an extra attempt, bounded by arq's cap)."""
        defer = _retry_backoff(attempt)
        if await _keep_job_key_alive(redis, job_id, defer) is False:
            await dead_letter_lost_record(error, kind)
        detail = (
            f"attempt {attempt} of {max_attempts} failed, retrying in {defer:.0f}s: {error}"
        )
        try:
            await redis.set(failures_key, json.dumps({**failures, kind: attempt}), ex=JOB_TTL)
            await redis.set(
                f"{JOB_STATUS_PREFIX}{job_id}",
                json.dumps({
                    **base_fields,
                    "status": "queued",
                    "attempt": failed_attempts + 1,
                    "detail": detail,
                }),
                ex=JOB_TTL,
            )
        except Exception:
            logger.warning(f"Could not record the retry of job {job_id}", exc_info=True)
        logger.warning(f"Function job {job_id}: {detail}")
        raise Retry(defer=defer)

    async def record_failure(error: str) -> None:
        """Row FAILED, callback, batch — for failures the engine did not
        record itself."""
        await executor.record_failure(
            execution_id=execution_id,
            error=error,
            user_id=user_id,
            trigger_id=trigger_id,
            function_namespace=function_namespace,
            function_name=function_name,
            callback_url=callback_url,
        )

    async def dead_letter_lost_record(error: str, error_type: str) -> None:
        """The job must be deferred, but arq's record of it is gone (it
        expired or was evicted), so a re-run would be dropped unseen. Fail
        it now, into the dead-letter queue, from which it can be requeued."""
        error = f"{error} (not deferred: the job's queue record expired from Redis)"
        logger.error(f"Function job {job_id} failed: {error}")
        await record_failure(f"{error}{dead_letter_note(failed_attempts + 1)}")
        await fail_job(error, dead_letter=True, error_type=error_type)
        raise JobRecordLost(error)

    async def fail_job(error: str, *, dead_letter: bool, error_type: str) -> None:
        """Terminal failure: final status, notify waiters, dead-letter."""
        attempts = failed_attempts + 1
        await redis.set(
            f"{JOB_STATUS_PREFIX}{job_id}",
            json.dumps({
                **base_fields,
                "status": "failed",
                "error": error,
                "attempts": attempts,
                "dead_lettered": dead_letter,
            }),
            ex=JOB_TTL,
        )
        await redis.publish(
            f"{JOB_DONE_CHANNEL_PREFIX}{execution_id}",
            json.dumps({"status": "failed", "error": error}),
        )
        await redis.delete(failures_key)
        if dead_letter:
            # Full kwargs, so POST /queue/dlq/{job_id}/retry can requeue it
            await push_dead_letter(
                redis,
                json.dumps({
                    "job_id": job_id,
                    "function": fn_label,
                    "function_namespace": function_namespace,
                    "function_name": function_name,
                    "execution_id": execution_id,
                    "input_data": input_data,
                    "trigger_type": trigger_type,
                    "trigger_id": trigger_id,
                    "user_id": user_id,
                    "chat_id": chat_id,
                    "callback_url": callback_url,
                    "depth": depth,
                    # The original request's attribution context, so a
                    # requeue runs with it rather than the admin's.
                    "credential_context": kwargs.get("credential_context"),
                    "error": error,
                    "error_type": error_type,
                    "attempts": attempts,
                    "failed_at": time.time(),
                }, default=str),
            )
            logger.warning(f"Job {job_id} moved to DLQ after {attempts} attempt(s): {error}")

    completed = False
    _span_ctx = {"context": parent_ctx} if parent_ctx else {}
    _fn_span = _tracer.start_span(
        "function.job",
        **_span_ctx,
        kind=otel_trace.SpanKind.CONSUMER,
        attributes={
            "function.name": f"{function_namespace}/{function_name}",
            "job.id": job_id,
            "job.queue": "functions",
            "job.try": job_try,
            "job.failed_attempts": failed_attempts,
        },
    )
    # Current for the job, so what the execution does (queries, HTTP calls,
    # the function's own spans) is recorded under it.
    _fn_token = otel_context.attach(otel_trace.set_span_in_context(_fn_span, parent_ctx))
    try:
        result = await executor.execute_function(
            function_namespace=function_namespace,
            function_name=function_name,
            input_data=input_data,
            execution_id=execution_id,
            trigger_type=trigger_type,
            trigger_id=trigger_id,
            user_id=user_id,
            chat_id=chat_id,
            callback_url=callback_url,
            depth=depth,
            prior_failures=failures,
        )

        # Store result
        await redis.set(
            f"{JOB_RESULT_PREFIX}{job_id}",
            json.dumps(result, default=str),
            ex=JOB_TTL,
        )

        # Update status to completed
        await redis.set(
            f"{JOB_STATUS_PREFIX}{job_id}",
            json.dumps({**base_fields, "status": "completed"}),
            ex=JOB_TTL,
        )
        if failed_attempts:
            await redis.delete(failures_key)

        # Notify waiters via pub/sub
        await redis.publish(
            f"{JOB_DONE_CHANNEL_PREFIX}{execution_id}",
            json.dumps({"status": "completed", "result": result}, default=str),
        )

        _fn_span.set_attribute("function.result", json.dumps(result, default=str) if result else "")
        completed = True
        logger.info(f"Function job {job_id} completed successfully")
        return result

    except SharedPoolSaturated as e:
        # Backpressure, not failure: the engine left the Execution row
        # PENDING. Defer the job and let arq re-run it as slots free — the
        # queue is the waiting room, so bulk ingest (thousands of uploads
        # onto a small pool) drains over however long it takes. The
        # time bound only exists so a permanently wedged pool eventually
        # fails loudly; 0 = wait forever. Deferrals never count as failed
        # attempts, so they cannot use up a job's retries.
        deferrals = max(1, job_try - failed_attempts)
        enqueued_at = base_fields.get("enqueued_at") or time.time()
        base_fields["enqueued_at"] = enqueued_at  # start the clock if the status key expired
        elapsed = time.time() - float(enqueued_at)
        window = settings.queue_saturation_timeout_seconds
        if window <= 0 or elapsed < window:
            defer = min(2 ** deferrals, 30) + random.uniform(0, 2)
            if await _keep_job_key_alive(redis, job_id, defer) is False:
                record_error(_fn_span, e)
                completed = True
                await dead_letter_lost_record(
                    f"{e} (pool saturated for {elapsed:.0f}s)", "saturation"
                )
            await redis.set(
                f"{JOB_STATUS_PREFIX}{job_id}",
                json.dumps({
                    **base_fields,
                    "status": "queued",
                    "detail": (
                        f"shared pool saturated; waited {elapsed:.0f}s, "
                        f"retrying in {defer:.0f}s"
                    ),
                }),
                ex=JOB_TTL,
            )
            logger.info(
                f"Function job {job_id} deferred {defer:.0f}s "
                f"(pool saturated {elapsed:.0f}s, deferral {deferrals})"
            )
            completed = True
            raise Retry(defer=defer)

        # Window exhausted — now it IS a failure, and a retryable one (the
        # code never ran): dead-letter it. The engine skipped its failure
        # bookkeeping for saturation, so do it here.
        record_error(_fn_span, e)
        error = f"{e} (pool saturated for {elapsed:.0f}s, window {window}s)"
        logger.error(f"Function job {job_id} failed: {error}")
        await record_failure(f"{error}{dead_letter_note(failed_attempts + 1)}")
        await fail_job(error, dead_letter=True, error_type="saturation")
        completed = True
        raise

    except ExecutionRetryScheduled as e:
        # The engine left the row PENDING with the failure noted.
        completed = True
        await schedule_retry(str(e), e.kind, e.attempt, e.max_attempts)

    except ExecutorUnavailable as e:
        # Infrastructure failure before the engine took over (database
        # unreachable while preparing the run): nothing ran, nothing recorded.
        infra = failures.get(INFRASTRUCTURE, 0)
        if infra < settings.queue_max_retries:
            completed = True
            await schedule_retry(str(e), INFRASTRUCTURE, infra + 1, settings.queue_max_retries + 1)
        record_error(_fn_span, e)
        logger.error(f"Function job {job_id} failed: {e}")
        await record_failure(f"{e}{dead_letter_note(failed_attempts + 1)}")
        await fail_job(str(e), dead_letter=True, error_type="infrastructure")
        completed = True
        raise

    except Exception as e:
        record_error(_fn_span, e)
        logger.error(f"Function job {job_id} failed: {e}")

        if not getattr(e, "recorded", False):
            # Failed before or outside the engine's own bookkeeping (the
            # function is gone, an unexpected error): make sure the row
            # doesn't stay PENDING forever.
            await record_failure(str(e))
        dead_letter = bool(getattr(e, "dead_letter", False))
        await fail_job(
            str(e),
            dead_letter=dead_letter,
            error_type="infrastructure" if getattr(e, "transient", False) else "function",
        )
        completed = True
        # Final: arq only re-runs on Retry, so this is not retried.
        raise

    finally:
        # Catch CancelledError/timeout — in Python 3.9+ CancelledError is a
        # BaseException, not Exception, so the except block above misses it.
        # Ensure job status is always updated so it doesn't stay "running" forever.
        if not completed:
            logger.warning(f"Function job {job_id} cancelled/timed out")
            from app.core.telemetry import mark_error

            mark_error(_fn_span, "Job cancelled or timed out", "cancelled")
            try:
                await redis.set(
                    f"{JOB_STATUS_PREFIX}{job_id}",
                    json.dumps({**base_fields, "status": "failed", "error": "Job cancelled or timed out"}),
                    ex=JOB_TTL,
                )
                await redis.publish(
                    f"{JOB_DONE_CHANNEL_PREFIX}{execution_id}",
                    json.dumps({"status": "failed", "error": "Job cancelled or timed out"}),
                )
            except Exception:
                logger.error(f"Failed to update status for cancelled job {job_id}")
        otel_context.detach(_fn_token)
        _fn_span.end()


async def function_worker_startup(ctx: dict) -> None:
    """arq startup hook for function workers.

    Initializes Redis, discovers existing shared worker containers (created
    by the backend), and starts the per-user container cleanup task.
    """
    from app.core.telemetry import init_telemetry
    init_telemetry("queue-worker")

    ctx["redis"] = create_redis_client()
    await startup_redis_checks(ctx["redis"], "queue-worker")

    # Discover shared worker containers (created by scheduler).
    # Retry a few times — the scheduler may still be starting up.
    # Skipped for non-Docker executors (k8s / single-container).
    if settings.code_execution_enabled and settings.trusted_executor == "docker_shared":
        from app.services.shared_worker_manager import shared_worker_manager

        for attempt in range(10):
            await shared_worker_manager._discover_existing_workers()
            if shared_worker_manager.workers:
                break
            if attempt < 9:
                print(f"⏳ No shared containers found, waiting for scheduler... ({attempt + 1}/10)")
                await asyncio.sleep(3)

        shared_worker_manager._initialized = True
        print(f"✅ Discovered {len(shared_worker_manager.workers)} shared containers")

    # Discover existing sandbox containers (created by backend leader)
    if settings.code_execution_enabled and settings.sandbox_executor == "docker_pool":
        from app.services.container_pool import container_pool

        await container_pool._discover_existing_containers()
        container_pool._initialized = True
        print(f"✅ Discovered {len(container_pool.idle)} sandbox containers")

    # Start heartbeat
    worker_id = str(uuid.uuid4())
    ctx["worker_id"] = worker_id
    heartbeat_data = {
        "worker_id": worker_id,
        "queue": "functions",
        "max_jobs": settings.queue_function_concurrency,
        "started_at": time.time(),
        "last_heartbeat": time.time(),
    }
    ctx["_heartbeat_task"] = asyncio.create_task(
        _heartbeat_loop(ctx["redis"], worker_id, heartbeat_data)
    )

    logger.info(f"Function worker started (id={worker_id})")


async def agent_worker_startup(ctx: dict) -> None:
    """arq startup hook for agent workers."""
    await _agent_worker_startup(ctx, "queue-agent")


async def subagent_worker_startup(ctx: dict) -> None:
    """arq startup hook for the sub-agent (delegated) workers."""
    await _agent_worker_startup(ctx, "queue-agent-sub")


async def _agent_worker_startup(ctx: dict, process: str) -> None:
    """Initializes Redis and eagerly imports app modules to avoid cold-start
    latency on the first job.
    """
    from app.core.telemetry import init_telemetry
    init_telemetry(process)

    ctx["redis"] = create_redis_client()
    await startup_redis_checks(ctx["redis"], process)

    # Eagerly import heavy modules so first job doesn't pay import cost
    from app.services.message_service import MessageService  # noqa: F401
    from app.core.database import AsyncSessionLocal  # noqa: F401

    # Discover sandbox containers (needed for code execution tool)
    if settings.code_execution_enabled and settings.sandbox_executor == "docker_pool":
        from app.services.container_pool import container_pool

        await container_pool._discover_existing_containers()
        container_pool._initialized = True
        print(f"✅ Agent worker discovered {len(container_pool.idle)} sandbox containers")

    # Start heartbeat
    worker_id = str(uuid.uuid4())
    ctx["worker_id"] = worker_id
    heartbeat_data = {
        "worker_id": worker_id,
        "queue": "agents",
        "max_jobs": settings.queue_agent_concurrency,
        "started_at": time.time(),
        "last_heartbeat": time.time(),
    }
    ctx["_heartbeat_task"] = asyncio.create_task(
        _heartbeat_loop(ctx["redis"], worker_id, heartbeat_data)
    )

    logger.info(f"Agent worker started (id={worker_id})")


def _failed_pipeline_run(result: Any) -> Optional[str]:
    """A pipeline run that ended failed or timed out (it returns, not raises)."""
    if isinstance(result, dict) and result.get("status") in ("failed", "timed_out"):
        return str(result.get("error") or f"Pipeline run {result['status']}")
    return None


@traced_job("pipeline.run", failed=_failed_pipeline_run)
async def execute_pipeline_run_job(ctx: dict, **kwargs: Any) -> Any:
    """Execute one pipeline run (one user scope) in the pipeline worker."""
    from app.services import pipeline_runner

    pipeline_id = kwargs["pipeline_id"]
    user_id = kwargs["user_id"]

    token = await pipeline_runner.mint_run_token(user_id)
    if not token:
        logger.error(f"Pipeline run job: user {user_id} not found, dropping run")
        return {"status": "failed", "error": f"User {user_id} not found"}

    return await pipeline_runner.run_pipeline(
        pipeline_id,
        kwargs.get("run_input"),
        run_id=kwargs.get("run_id"),
        trigger_type=kwargs.get("trigger_type", "API"),
        trigger_id=kwargs.get("trigger_id"),
        user_id=user_id,
        user_token=token,
        sync=False,
    )


@traced_job("pipeline.fire")
async def execute_pipeline_fire_job(ctx: dict, **kwargs: Any) -> Any:
    """Expand a trigger firing into per-scope run jobs (perUser fan-out)."""
    from app.services import pipeline_runner

    job_ids = await pipeline_runner.fire_pipeline(
        kwargs["namespace"],
        kwargs["name"],
        kwargs.get("run_input"),
        trigger_type=kwargs.get("trigger_type", "API"),
        trigger_id=kwargs.get("trigger_id"),
    )
    return {"runs": len(job_ids), "job_ids": job_ids}


async def pipeline_worker_startup(ctx: dict) -> None:
    """arq startup hook for the pipeline worker.

    Pipeline runs are await-heavy orchestration (they wait on child function/
    agent executions and HTTP), so this worker runs many jobs concurrently and
    needs no container discovery.
    """
    from app.core.telemetry import init_telemetry
    init_telemetry("queue-pipeline")

    ctx["redis"] = create_redis_client()
    await startup_redis_checks(ctx["redis"], "queue-pipeline")

    # Eagerly import the runner so the first job doesn't pay import cost
    from app.services import pipeline_runner  # noqa: F401

    worker_id = str(uuid.uuid4())
    ctx["worker_id"] = worker_id
    heartbeat_data = {
        "worker_id": worker_id,
        "queue": "pipelines",
        "max_jobs": settings.queue_pipeline_concurrency,
        "started_at": time.time(),
        "last_heartbeat": time.time(),
    }
    ctx["_heartbeat_task"] = asyncio.create_task(
        _heartbeat_loop(ctx["redis"], worker_id, heartbeat_data)
    )

    logger.info(f"Pipeline worker started (id={worker_id})")


async def shutdown(ctx: dict) -> None:
    """arq worker shutdown hook."""
    # Cancel heartbeat
    task = ctx.get("_heartbeat_task")
    if task:
        task.cancel()

    # Remove heartbeat key
    redis = ctx.get("redis")
    worker_id = ctx.get("worker_id")
    if redis and worker_id:
        try:
            await redis.delete(f"{WORKER_HEARTBEAT_PREFIX}{worker_id}")
        except Exception:
            pass
        await redis.aclose()

    # Execution log rows still buffered for ClickHouse
    from app.services.clickhouse_logger import clickhouse_logger

    await clickhouse_logger.aclose()

    # LLM clients this worker opened (agent jobs)
    from app.providers.clients import close_shared_clients

    await close_shared_clients()

    from app.core.telemetry import flush_telemetry

    flush_telemetry()

    logger.info("Worker stopped")


@traced_job("code.background")
async def execute_background_code_job(ctx: dict, **kwargs: Any) -> None:
    """A run_in_background sandbox run: executes, then either completes the
    suspended round (wait=true) or delivers the result into the chat. Runs in
    the trace context it was enqueued with (`traced_job`)."""
    from app.services.chat_wakeups import run_background_code_job

    await run_background_code_job(
        job_id=kwargs["job_id"], chat_id=kwargs["chat_id"], user_id=kwargs["user_id"],
        code=kwargs["code"], description=kwargs.get("description") or "background run",
        tool_call_id=kwargs.get("tool_call_id"), pending_completion_id=kwargs.get("pending_completion_id"),
        api_key_id=kwargs.get("api_key_id"),
    )


def _background_job_timeout() -> int:
    return settings.code_execution_timeout + settings.background_job_grace_seconds


class WorkerSettings:
    """arq worker settings for function execution."""

    functions = [
        execute_function_job,
        # arq times jobs per function (enqueue_job takes no timeout): a
        # background run gets code_execution_timeout plus the grace a waiting
        # round allows it, not the queue default.
        func(execute_background_code_job, timeout=_background_job_timeout()),
    ]
    on_startup = function_worker_startup
    on_shutdown = shutdown
    redis_settings = get_redis_settings()
    queue_name = "sinas:queue:functions"
    poll_delay = settings.queue_poll_delay
    max_jobs = settings.queue_function_concurrency
    job_timeout = settings.queue_default_timeout
    # arq counts every run in job_try, deferrals and failed attempts alike;
    # execute_function_job enforces both budgets itself (the saturation
    # window, the failure count). arq's cap is only a backstop and must
    # never fire first, or a job dies without its failure bookkeeping:
    # the worst-case deferral count (≈ window / 30s backoff cap) plus the
    # most failed attempts any job may make — both budgets, since a job can
    # use up its infrastructure retries and its function retries — plus the
    # final run. For window=0 (wait forever), an effectively unlimited budget.
    max_tries = (
        (
            settings.queue_saturation_timeout_seconds // 30 + 10
            if settings.queue_saturation_timeout_seconds > 0
            else 1_000_000
        )
        + settings.queue_max_retries
        + MAX_FUNCTION_RETRIES
        + 1
    )


# Import agent jobs for combined worker
from app.queue.agent_jobs import (
    agent_function_timeout,
    execute_agent_delegate_resume_job,
    execute_agent_message_job,
    execute_agent_resume_job,
)


async def sweep_deferred_expiry_job(ctx: dict) -> int:
    """Resolve pending completions/approvals whose deadline passed, so a
    suspended tool round can never hang forever (the expired entries get
    timeout-error tool results and the round resumes)."""
    from app.services import deferred_completions

    return await deferred_completions.expire_due()


async def deliver_due_wakeups_job(ctx: dict) -> int:
    """Deliver wakeup timers whose due time passed (schedule_wakeup)."""
    from app.services.chat_wakeups import deliver_due_timers

    return await deliver_due_timers()


async def poll_mcp_tasks_job(ctx: dict) -> int:
    """Poll MCP tasks whose next poll is due; deliver finished ones (MCP Tasks)."""
    from app.services.mcp_tasks import poll_due_tasks

    return await poll_due_tasks()


async def refresh_package_repositories_job(ctx: dict) -> int:
    """Refresh package repository indexes older than the refresh interval."""
    from app.services.package_repositories import refresh_due

    return await refresh_due()


async def refresh_model_prices_job(ctx: dict) -> bool:
    """Refresh the MODEL_PRICES_URL ratecard when it is due."""
    from app.services.llm_pricing import refresh_url_ratecard_due

    return await refresh_url_ratecard_due()


async def chat_retention_tick_job(ctx: dict) -> bool:
    """Start a chat retention run when CHAT_RETENTION_SCHEDULE says so (or
    continue one a budget cut short). Does nothing unless retention is
    enabled. See docs/design/chat-retention.md."""
    from app.services.retention.job import scheduled_tick

    return await scheduled_tick() is not None


@traced_job("retention.run", failed=lambda status: "Retention run failed" if status == "failed" else None)
async def chat_retention_run_job(ctx: dict, run_id: str) -> str:
    """A run queued by POST /retention/run ("Run now")."""
    import uuid as _uuid

    from app.services.retention.job import run_retention

    result = await run_retention(trigger="api", run_id=_uuid.UUID(run_id))
    return result["status"]


def _retention_job_timeout() -> int:
    return (settings.chat_retention_max_run_seconds or 6 * 3600) + 600


class AgentWorkerSettings:
    """arq worker settings for agent message processing."""

    # Each agent job enforces its own job_timeout (the chat's or agent's,
    # else AGENT_JOB_TIMEOUT; arq has no per-job timeout); arq's, per
    # function, is only the backstop above the longest one allowed.
    functions = [
        func(execute_agent_message_job, timeout=agent_function_timeout()),
        func(execute_agent_resume_job, timeout=agent_function_timeout()),
        func(execute_agent_delegate_resume_job, timeout=agent_function_timeout()),
        func(chat_retention_run_job, timeout=_retention_job_timeout(), max_tries=1),
    ]
    # Deferred-round expiry sweep: minutely, deduplicated across workers
    # (arq cron unique=True). Runs here — not on the sub-agent worker — so
    # exactly one worker class owns it.
    cron_jobs = [
        cron(sweep_deferred_expiry_job, second=0, unique=True),
        cron(deliver_due_wakeups_job, second=30, unique=True),
        # Every 5 seconds; each task is polled at its own (server-suggested)
        # interval, at least mcp_task_min_poll_seconds apart.
        cron(poll_mcp_tasks_job, second=set(range(0, 60, 5)), unique=True),
        # Hourly; each repository is refreshed when its index is older than
        # package_repository_refresh_hours.
        cron(refresh_package_repositories_job, minute=17, second=10, unique=True),
        # Every 10 minutes; fetches only when never fetched, the URL changed
        # or the copy is older than model_prices_refresh_hours (so a failing
        # URL is retried every 10 minutes, the last good copy kept meanwhile).
        cron(refresh_model_prices_job, minute=set(range(3, 60, 10)), second=40, unique=True),
        # Every 10 minutes; starts a run only when CHAT_RETENTION_SCHEDULE
        # fired since the last scheduled run (or that one ended "partial").
        cron(
            chat_retention_tick_job,
            minute=set(range(1, 60, 10)),
            second=20,
            unique=True,
            timeout=_retention_job_timeout(),
            max_tries=1,
        ),
    ]
    on_startup = agent_worker_startup
    on_shutdown = shutdown
    redis_settings = get_redis_settings()
    queue_name = "sinas:queue:agents"
    poll_delay = settings.queue_poll_delay
    max_jobs = settings.queue_agent_concurrency
    job_timeout = settings.agent_job_timeout  # the cron jobs'; agent jobs time themselves (above)
    max_tries = 1  # No retry for agent conversations (side effects)


class PipelineWorkerSettings:
    """arq worker settings for pipeline runs. Own queue + process so long
    pipeline runs never starve function workers (and vice versa). High
    concurrency: runs mostly await child executions and HTTP.

        python -m app.queue.arq_worker app.queue.worker.PipelineWorkerSettings
    """

    functions = [execute_pipeline_run_job, execute_pipeline_fire_job]
    on_startup = pipeline_worker_startup
    on_shutdown = shutdown
    redis_settings = get_redis_settings()
    queue_name = "sinas:queue:pipelines"
    poll_delay = settings.queue_poll_delay
    max_jobs = settings.queue_pipeline_concurrency
    job_timeout = settings.pipeline_job_timeout
    max_tries = 1  # runs are re-fired by triggers; never auto-retried (side effects)


class SubAgentWorkerSettings:
    """arq worker settings for DELEGATED agent jobs (issue #90).

    Same job handlers as AgentWorkerSettings, different queue: agent-to-agent
    calls (depth > 0) land here so parents blocked on children can never
    starve them of worker slots. Run as its own process:

        python -m app.queue.arq_worker app.queue.worker.SubAgentWorkerSettings
    """

    functions = [
        func(execute_agent_message_job, timeout=agent_function_timeout()),
        func(execute_agent_resume_job, timeout=agent_function_timeout()),
        func(execute_agent_delegate_resume_job, timeout=agent_function_timeout()),
    ]
    on_startup = subagent_worker_startup
    on_shutdown = shutdown
    redis_settings = get_redis_settings()
    queue_name = "sinas:queue:agents:sub"
    poll_delay = settings.queue_poll_delay
    max_jobs = settings.queue_agent_sub_concurrency
    job_timeout = settings.agent_job_timeout
    max_tries = 1  # No retry for agent conversations (side effects)
