"""arq worker definitions for function and agent execution."""
import asyncio
import json
import logging
import time
import uuid
from typing import Any

from app.core.config import settings
from app.core.redis import get_redis_settings
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
    resume_data = kwargs.get("resume_data")

    redis: Redis = ctx.get("redis") or Redis.from_url(settings.redis_url, decode_responses=True)

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
            resume_data=resume_data,
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

        logger.info(f"Function job {job_id} completed successfully")
        return result

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


async def function_worker_startup(ctx: dict) -> None:
    """arq startup hook for function workers.

    Initializes Redis, discovers existing shared worker containers (created
    by the backend), and starts the per-user container cleanup task.
    """
    from redis.asyncio import Redis

    ctx["redis"] = Redis.from_url(settings.redis_url, decode_responses=True)

    # Discover shared worker containers (created by scheduler).
    # Retry a few times — the scheduler may still be starting up.
    from app.services.shared_worker_manager import shared_worker_manager

    for attempt in range(10):
        await shared_worker_manager._discover_existing_workers()
        if shared_worker_manager.workers:
            break
        if attempt < 9:
            print(f"⏳ No shared workers found, waiting for scheduler... ({attempt + 1}/10)")
            await asyncio.sleep(3)

    shared_worker_manager._initialized = True
    print(f"✅ Discovered {len(shared_worker_manager.workers)} shared workers")

    # Discover existing pool containers (created by backend leader)
    from app.services.container_pool import container_pool

    await container_pool._discover_existing_containers()
    container_pool._initialized = True
    print(f"✅ Discovered {len(container_pool.idle)} pool containers")

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

    ctx["redis"] = Redis.from_url(settings.redis_url, decode_responses=True)

    # Eagerly import heavy modules so first job doesn't pay import cost
    from app.services.message_service import MessageService  # noqa: F401
    from app.core.database import AsyncSessionLocal  # noqa: F401

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
    max_tries = settings.queue_max_retries
    retry_delay = settings.queue_retry_delay


# Import agent jobs for combined worker
from app.queue.agent_jobs import (
    execute_agent_message_job,
    execute_agent_resume_job,
)


class AgentWorkerSettings:
    """arq worker settings for agent message processing."""

    functions = [execute_agent_message_job, execute_agent_resume_job]
    on_startup = agent_worker_startup
    on_shutdown = shutdown
    redis_settings = get_redis_settings()
    queue_name = "sinas:queue:agents"
    max_jobs = settings.queue_agent_concurrency
    job_timeout = 600  # 10 minutes for long conversations
    max_tries = 1  # No retry for agent conversations (side effects)
