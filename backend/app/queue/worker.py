"""arq worker definitions for function and agent execution."""
import asyncio
import json
import logging
import random
import time
import uuid
from typing import Any

from arq.worker import Retry

from app.core.config import settings
from app.core.redis import get_redis_settings
from app.services.shared_admission import SharedPoolSaturated
from app.services.queue_service import (
    DLQ_KEY,
    JOB_RESULT_PREFIX,
    JOB_STATUS_PREFIX,
    JOB_TTL,
    JOB_DONE_CHANNEL_PREFIX,
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


async def execute_function_job(ctx: dict, **kwargs: Any) -> Any:
    """
    Execute a function in the worker process.

    Called by arq when a function job is dequeued.
    Delegates to the existing executor.execute_function().
    """
    from redis.asyncio import Redis

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

    redis: Redis = ctx.get("redis") or Redis.from_url(settings.redis_url, decode_responses=True)

    # Restore trace context from the enqueue side
    from app.core.telemetry import extract_trace_context, get_tracer, otel_attr
    parent_ctx = extract_trace_context(kwargs.get("trace_context", {}))
    _tracer = get_tracer()

    logger.info(
        f"Worker executing function {function_namespace}/{function_name} "
        f"(job={job_id}, execution={execution_id})"
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

    # Update status to running
    await redis.set(
        f"{JOB_STATUS_PREFIX}{job_id}",
        json.dumps({**base_fields, "status": "running"}),
        ex=JOB_TTL,
    )

    completed = False
    _span_ctx = {"context": parent_ctx} if parent_ctx else {}
    _fn_span = _tracer.start_span(
        "function.job",
        **_span_ctx,
        attributes={
            "function.name": f"{function_namespace}/{function_name}",
            "job.id": job_id,
            "job.queue": "functions",
        },
    )
    try:
        from app.services.execution_engine import executor

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
        # fails loudly; 0 = wait forever.
        job_try = ctx.get("job_try", 1)
        enqueued_at = base_fields.get("enqueued_at") or time.time()
        base_fields["enqueued_at"] = enqueued_at  # start the clock if the status key expired
        elapsed = time.time() - float(enqueued_at)
        window = settings.queue_saturation_timeout_seconds
        if window <= 0 or elapsed < window:
            defer = min(2 ** job_try, 30) + random.uniform(0, 2)
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
                f"(pool saturated {elapsed:.0f}s, attempt {job_try})"
            )
            completed = True
            raise Retry(defer=defer)

        # Window exhausted — now it IS a failure. The engine skipped its
        # failure bookkeeping for saturation, so do it here.
        logger.error(
            f"Function job {job_id} failed: pool saturated for {elapsed:.0f}s "
            f"(window {window}s)"
        )
        from app.core.database import AsyncSessionLocal
        from app.models.execution import Execution, ExecutionStatus
        from sqlalchemy import select as _select
        from datetime import datetime as _dt

        async with AsyncSessionLocal() as _db:
            row = (
                await _db.execute(
                    _select(Execution).where(Execution.execution_id == execution_id)
                )
            ).scalar_one_or_none()
            if row and row.status == ExecutionStatus.PENDING:
                row.status = ExecutionStatus.FAILED
                row.error = str(e)
                row.completed_at = _dt.utcnow()
                await _db.commit()

        await redis.set(
            f"{JOB_STATUS_PREFIX}{job_id}",
            json.dumps({**base_fields, "status": "failed", "error": str(e)}),
            ex=JOB_TTL,
        )
        await redis.publish(
            f"{JOB_DONE_CHANNEL_PREFIX}{execution_id}",
            json.dumps({"status": "failed", "error": str(e)}),
        )
        completed = True
        raise

    except Exception as e:
        logger.error(f"Function job {job_id} failed: {e}")

        # Update status to failed
        await redis.set(
            f"{JOB_STATUS_PREFIX}{job_id}",
            json.dumps({**base_fields, "status": "failed", "error": str(e)}),
            ex=JOB_TTL,
        )

        # Notify waiters of failure
        await redis.publish(
            f"{JOB_DONE_CHANNEL_PREFIX}{execution_id}",
            json.dumps({"status": "failed", "error": str(e)}),
        )

        completed = True

        # Check if retries exhausted (arq handles retry count internally)
        job_try = ctx.get("job_try", 1)
        if job_try >= settings.queue_max_retries:
            # Push to dead letter queue (include full kwargs for retry)
            await redis.lpush(
                DLQ_KEY,
                json.dumps({
                    "job_id": job_id,
                    "function": f"{function_namespace}/{function_name}",
                    "function_namespace": function_namespace,
                    "function_name": function_name,
                    "execution_id": execution_id,
                    "input_data": input_data,
                    "trigger_type": trigger_type,
                    "trigger_id": trigger_id,
                    "user_id": user_id,
                    "chat_id": chat_id,
                    "error": str(e),
                    "attempts": job_try,
                }, default=str),
            )
            logger.warning(f"Job {job_id} moved to DLQ after {job_try} attempts")

        raise  # Re-raise for arq retry

    finally:
        # Catch CancelledError/timeout — in Python 3.9+ CancelledError is a
        # BaseException, not Exception, so the except block above misses it.
        # Ensure job status is always updated so it doesn't stay "running" forever.
        if not completed:
            logger.warning(f"Function job {job_id} cancelled/timed out")
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
        _fn_span.end()


async def function_worker_startup(ctx: dict) -> None:
    """arq startup hook for function workers.

    Initializes Redis, discovers existing shared worker containers (created
    by the backend), and starts the per-user container cleanup task.
    """
    from redis.asyncio import Redis

    from app.core.telemetry import init_telemetry
    init_telemetry()

    ctx["redis"] = Redis.from_url(settings.redis_url, decode_responses=True)

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
    """arq startup hook for agent workers.

    Initializes Redis and eagerly imports app modules to avoid cold-start
    latency on the first job.
    """
    from redis.asyncio import Redis

    from app.core.telemetry import init_telemetry
    init_telemetry()

    ctx["redis"] = Redis.from_url(settings.redis_url, decode_responses=True)

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
    from redis.asyncio import Redis

    from app.core.telemetry import init_telemetry
    init_telemetry()

    ctx["redis"] = Redis.from_url(settings.redis_url, decode_responses=True)

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

    logger.info("Worker stopped")


class WorkerSettings:
    """arq worker settings for function execution."""

    functions = [execute_function_job]
    on_startup = function_worker_startup
    on_shutdown = shutdown
    redis_settings = get_redis_settings()
    queue_name = "sinas:queue:functions"
    max_jobs = settings.queue_function_concurrency
    job_timeout = settings.queue_default_timeout
    # Must exceed the worst-case saturation attempt count (≈ window / 30s
    # backoff cap) or arq's own cap kills a deferred job before our explicit
    # exhausted-path bookkeeping runs. Plain exceptions are unaffected: arq
    # only re-runs jobs that raise Retry. For window=0 (wait forever), give
    # arq an effectively unlimited budget.
    max_tries = max(
        settings.queue_max_retries,
        (settings.queue_saturation_timeout_seconds // 30 + 10)
        if settings.queue_saturation_timeout_seconds > 0
        else 1_000_000,
    )
    retry_delay = settings.queue_retry_delay


# Import agent jobs for combined worker
from app.queue.agent_jobs import (
    execute_agent_delegate_resume_job,
    execute_agent_message_job,
    execute_agent_resume_job,
)


class AgentWorkerSettings:
    """arq worker settings for agent message processing."""

    functions = [
        execute_agent_message_job,
        execute_agent_resume_job,
        execute_agent_delegate_resume_job,
    ]
    on_startup = agent_worker_startup
    on_shutdown = shutdown
    redis_settings = get_redis_settings()
    queue_name = "sinas:queue:agents"
    max_jobs = settings.queue_agent_concurrency
    job_timeout = settings.agent_job_timeout  # Default timeout, can be overridden per-job
    max_tries = 1  # No retry for agent conversations (side effects)


class PipelineWorkerSettings:
    """arq worker settings for pipeline runs. Own queue + process so long
    pipeline runs never starve function workers (and vice versa). High
    concurrency: runs mostly await child executions and HTTP.

        python -m arq app.queue.worker.PipelineWorkerSettings
    """

    functions = [execute_pipeline_run_job, execute_pipeline_fire_job]
    on_startup = pipeline_worker_startup
    on_shutdown = shutdown
    redis_settings = get_redis_settings()
    queue_name = "sinas:queue:pipelines"
    max_jobs = settings.queue_pipeline_concurrency
    job_timeout = settings.pipeline_job_timeout
    max_tries = 1  # runs are re-fired by triggers; never auto-retried (side effects)


class SubAgentWorkerSettings:
    """arq worker settings for DELEGATED agent jobs (issue #90).

    Same job handlers as AgentWorkerSettings, different queue: agent-to-agent
    calls (depth > 0) land here so parents blocked on children can never
    starve them of worker slots. Run as its own process:

        python -m arq app.queue.worker.SubAgentWorkerSettings
    """

    functions = [
        execute_agent_message_job,
        execute_agent_resume_job,
        execute_agent_delegate_resume_job,
    ]
    on_startup = agent_worker_startup
    on_shutdown = shutdown
    redis_settings = get_redis_settings()
    queue_name = "sinas:queue:agents:sub"
    max_jobs = settings.queue_agent_sub_concurrency
    job_timeout = settings.agent_job_timeout
    max_tries = 1  # No retry for agent conversations (side effects)
