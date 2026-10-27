"""Queue service for dispatching function and agent jobs via arq."""
import asyncio
import json
import logging
import time
from datetime import timedelta
import uuid
from typing import Any, Optional

from app.core.auth import current_api_key_id, current_credential_context
from app.core.config import settings
from app.core.redis import get_arq_pool, get_redis
from app.core.telemetry import inject_job_context

logger = logging.getLogger(__name__)

# Redis key prefixes
JOB_STATUS_PREFIX = "sinas:job:status:"
AGENT_QUEUE = "sinas:queue:agents"
# Dedicated queue for delegated (agent-to-agent) jobs — see issue #90.
SUB_AGENT_QUEUE = "sinas:queue:agents:sub"
# Pipeline runs — own queue so long runs never starve function workers.
PIPELINE_QUEUE = "sinas:queue:pipelines"
JOB_RESULT_PREFIX = "sinas:job:result:"
JOB_DONE_CHANNEL_PREFIX = "sinas:job:done:"
DLQ_KEY = "sinas:queue:dlq"
# How many dead-lettered entries the DLQ cap (QUEUE_DLQ_MAX_ENTRIES) dropped.
DLQ_DROPPED_KEY = "sinas:queue:dlq:dropped"
# How many runs of a function job failed so far. Kept apart from arq's
# job_try, which shared-pool deferrals advance too: a deferral is not a
# failure and must never use up a job's retries.
JOB_FAILURES_PREFIX = "sinas:job:failures:"
# Held while a dead-lettered job is being requeued (one requeue at a time).
DLQ_REQUEUE_LOCK_PREFIX = "sinas:queue:dlq:requeue:"
JOB_TTL = 86400  # 24 hours


async def push_dead_letter(redis, entry: str) -> int:
    """Add an entry (JSON) to the dead-letter queue, newest first, and keep
    the queue within QUEUE_DLQ_MAX_ENTRIES by dropping the oldest entries.

    The push and the trim run in one MULTI, so the length LPUSH reports is
    exactly what this trim cuts back from: concurrent pushes count every
    dropped entry once. Returns how many entries were dropped."""
    cap = settings.queue_dlq_max_entries
    if cap <= 0:
        await redis.lpush(DLQ_KEY, entry)
        return 0
    async with redis.pipeline(transaction=True) as pipe:
        pipe.lpush(DLQ_KEY, entry)
        pipe.ltrim(DLQ_KEY, 0, cap - 1)
        length, _ = await pipe.execute()
    dropped = max(0, int(length) - cap)
    if dropped:
        try:
            await redis.incrby(DLQ_DROPPED_KEY, dropped)
        except Exception:
            logger.warning("Could not count the dropped DLQ entries", exc_info=True)
        logger.warning(
            f"Dead-letter queue is at its cap of {cap} (QUEUE_DLQ_MAX_ENTRIES); "
            f"dropped the oldest {dropped} entr{'y' if dropped == 1 else 'ies'}"
        )
    return dropped


async def trim_dead_letter_queue(redis) -> int:
    """Cut the dead-letter queue back to QUEUE_DLQ_MAX_ENTRIES (oldest go).

    `push_dead_letter` trims as it pushes, but at maxmemory Redis refuses
    the push, and with it the whole MULTI: a DLQ grown before the cap
    existed (pre-0.5.0) would never shrink on a full Redis. LLEN and LTRIM
    are never refused for memory (outside MULTI), so every worker and the
    scheduler run this once at startup. Returns how many entries were dropped."""
    cap = settings.queue_dlq_max_entries
    if cap <= 0:
        return 0
    # Not MULTI: Redis 6.2 refuses every command queued in a transaction
    # while it is over maxmemory. Without it the drop count is approximate
    # if entries arrive in between (only at startup, so rarely).
    length = int(await redis.llen(DLQ_KEY))
    if length <= cap:
        return 0
    await redis.ltrim(DLQ_KEY, 0, cap - 1)
    dropped = length - cap
    if dropped:
        try:
            await redis.incrby(DLQ_DROPPED_KEY, dropped)
        except Exception:
            logger.warning("Could not count the dropped DLQ entries", exc_info=True)
        logger.warning(
            f"Dead-letter queue held {length} entries, over its cap of {cap} "
            f"(QUEUE_DLQ_MAX_ENTRIES); dropped the oldest {dropped}"
        )
    return dropped


async def startup_redis_checks(redis, process: str) -> None:
    """Run by every queue worker and the scheduler as it starts: trim the
    DLQ to its cap, and say loudly when Redis is (nearly) full, the state
    an upgrade from an allkeys-lru Redis can start in. Never raises."""
    from app.core.redis import warn_if_redis_nearly_full

    try:
        await trim_dead_letter_queue(redis)
    except Exception:
        logger.warning(f"{process}: could not trim the dead-letter queue", exc_info=True)
    await warn_if_redis_nearly_full(redis, process)


class QueueService:
    """Service for enqueuing and tracking jobs."""

    async def enqueue_function(
        self,
        function_namespace: str,
        function_name: str,
        input_data: dict[str, Any],
        execution_id: str,
        trigger_type: str,
        trigger_id: str,
        user_id: str,
        chat_id: Optional[str] = None,
        delay: Optional[int] = None,
        callback_url: Optional[str] = None,
        depth: int = 0,
    ) -> str:
        """
        Enqueue a function execution job.

        Uses execution_id as the arq job_id so there's a single ID
        to track both the queue job and the execution record.

        Returns:
            execution_id (str) — same value passed in, now also the job_id
        """
        pool = await get_arq_pool()
        redis = await get_redis()

        # Use execution_id as job_id — single ID for both queue and execution
        job_id = execution_id

        fn_label = f"{function_namespace}/{function_name}" if function_name else f"resume:{execution_id[:8]}"

        # Set initial status in Redis
        await redis.set(
            f"{JOB_STATUS_PREFIX}{job_id}",
            json.dumps({
                "status": "queued",
                "execution_id": execution_id,
                "queue": "functions",
                "function": fn_label,
                "trigger_type": trigger_type,
                "enqueued_at": time.time(),
            }),
            ex=JOB_TTL,
        )

        # Build kwargs for arq job
        job_kwargs: dict[str, Any] = {
            "job_id": job_id,
            "function_namespace": function_namespace,
            "function_name": function_name,
            "input_data": input_data,
            "execution_id": execution_id,
            "trigger_type": trigger_type,
            "trigger_id": trigger_id,
            "user_id": user_id,
            "chat_id": chat_id,
            "callback_url": callback_url,
            "depth": depth,
            # The enqueuing request's attribution context: the function's
            # access token carries it, so what it starts through Sinas is
            # attributed as the request's would be.
            "credential_context": current_credential_context(),
        }

        # Enqueue with optional delay
        enqueue_kwargs = {"_job_id": job_id, "_queue_name": "sinas:queue:functions"}
        if delay:
            enqueue_kwargs["_defer_by"] = delay

        job = await pool.enqueue_job(
            "execute_function_job",
            **job_kwargs,
            trace_context=inject_job_context(),
            **enqueue_kwargs,
        )
        if job is None:
            # arq refuses a job id that is still queued, running, or has a
            # stored result; the job would silently never run.
            raise RuntimeError(f"Function job {job_id} is already queued or still has a result")

        logger.info(
            f"Enqueued function job: {function_namespace}/{function_name} "
            f"(execution_id={execution_id})"
        )
        return execution_id

    async def enqueue_pipeline_run(
        self,
        pipeline_id: str,
        run_input: Optional[dict[str, Any]],
        trigger_type: str,
        trigger_id: Optional[str],
        user_id: str,
    ) -> str:
        """Enqueue one pipeline run for one user scope. Returns the job id.

        The worker mints the run user's JWT itself — no tokens in job payloads.
        """
        pool = await get_arq_pool()
        job_id = str(uuid.uuid4())
        # Pre-allocate the RUN id and return that, not the job id: callers
        # poll GET /pipelines/runs/{run_id} with what we hand back, and the
        # job id never appears in pipeline_runs. (The run row itself is
        # created when the worker starts executing.)
        run_id = str(uuid.uuid4())
        await pool.enqueue_job(
            "execute_pipeline_run_job",
            job_id=job_id,
            run_id=run_id,
            pipeline_id=pipeline_id,
            run_input=run_input,
            trigger_type=trigger_type,
            trigger_id=trigger_id,
            user_id=user_id,
            trace_context=inject_job_context(),
            _job_id=job_id,
            _queue_name=PIPELINE_QUEUE,
        )
        logger.info(f"Enqueued pipeline run job {job_id} (run={run_id}, pipeline={pipeline_id})")
        return run_id

    async def enqueue_pipeline_fire(
        self,
        namespace: str,
        name: str,
        run_input: Optional[dict[str, Any]],
        trigger_type: str,
        trigger_id: Optional[str],
    ) -> str:
        """Enqueue a pipeline *fire* (trigger firing). The fire job expands to one
        run per user for perUser pipelines — triggers stay dumb."""
        pool = await get_arq_pool()
        job_id = str(uuid.uuid4())
        await pool.enqueue_job(
            "execute_pipeline_fire_job",
            job_id=job_id,
            namespace=namespace,
            name=name,
            run_input=run_input,
            trigger_type=trigger_type,
            trigger_id=trigger_id,
            trace_context=inject_job_context(),
            _job_id=job_id,
            _queue_name=PIPELINE_QUEUE,
        )
        logger.info(f"Enqueued pipeline fire job {job_id} ({namespace}/{name}, {trigger_type})")
        return job_id

    async def get_job_status(self, job_id: str) -> Optional[dict[str, Any]]:
        """Get job status from Redis."""
        redis = await get_redis()
        data = await redis.get(f"{JOB_STATUS_PREFIX}{job_id}")
        if data:
            return json.loads(data)
        return None

    async def get_job_result(self, job_id: str) -> Optional[Any]:
        """Get job result from Redis."""
        redis = await get_redis()
        data = await redis.get(f"{JOB_RESULT_PREFIX}{job_id}")
        if data:
            return json.loads(data)
        return None

    async def enqueue_and_wait(
        self,
        function_namespace: str,
        function_name: str,
        input_data: dict[str, Any],
        execution_id: str,
        trigger_type: str,
        trigger_id: str,
        user_id: str,
        chat_id: Optional[str] = None,
        timeout: Optional[int] = None,
        depth: int = 0,
    ) -> Any:
        """
        Enqueue a function job and wait for its result.

        Subscribes to the Redis pub/sub completion channel BEFORE enqueuing
        so that fast-completing jobs can't be missed.
        """
        redis = await get_redis()
        timeout = timeout or settings.queue_default_timeout

        # Subscribe BEFORE enqueuing to eliminate the race where the worker
        # publishes the result before we start listening.
        channel = f"{JOB_DONE_CHANNEL_PREFIX}{execution_id}"
        pubsub = redis.pubsub()
        await pubsub.subscribe(channel)

        try:
            job_id = await self.enqueue_function(
                function_namespace=function_namespace,
                function_name=function_name,
                input_data=input_data,
                execution_id=execution_id,
                trigger_type=trigger_type,
                trigger_id=trigger_id,
                user_id=user_id,
                chat_id=chat_id,
                depth=depth,
            )

            # Wait for result with timeout
            deadline = asyncio.get_event_loop().time() + timeout
            while asyncio.get_event_loop().time() < deadline:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg and msg["type"] == "message":
                    result_data = json.loads(msg["data"])
                    if result_data.get("status") == "failed":
                        raise Exception(result_data.get("error", "Job failed"))
                    if result_data.get("status") == "cancelled":
                        raise Exception("Job cancelled")
                    return result_data.get("result")

                # Fallback: check Redis status key in case pub/sub message
                # was delivered before subscription was fully active (unlikely
                # now but keeps the safety net).
                status = await self.get_job_status(job_id)
                if status and status.get("status") == "completed":
                    result = await self.get_job_result(job_id)
                    return result
                elif status and status.get("status") == "failed":
                    raise Exception(status.get("error", "Job failed"))

            raise TimeoutError(
                f"Job {job_id} timed out after {timeout}s"
            )
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()

    async def enqueue_agent_message(
        self,
        chat_id: str,
        user_id: str,
        user_token: str,
        content: str,
        channel_id: str,
        agent: Optional[str] = None,
        trigger_type: Optional[str] = None,
        job_timeout: Optional[int] = None,
        execution_id: Optional[str] = None,
        depth: int = 0,
        pending_delegation_id: Optional[str] = None,
        parent_tool_call_id: Optional[str] = None,
        job_id: Optional[str] = None,
    ) -> str:
        """Enqueue an agent message processing job.

        `job_id`, when given, makes the enqueue idempotent: arq refuses a
        second job with an id that is queued, running or recently finished,
        so a sender that retries after a crash cannot start the turn twice.

        If `execution_id` is provided, the worker will update that Execution
        row to COMPLETED/FAILED on terminal status (used by batch_service).

        `depth` is the agent-to-agent delegation depth (0 = user-initiated).
        Delegated jobs (depth > 0) go to the dedicated sub-agent queue so
        parents waiting on children can never starve them of worker slots
        (issue #90). `pending_delegation_id`/`parent_tool_call_id` are set for
        suspend-mode delegations: on terminal status the worker reports the
        result back to the suspended parent.
        """
        pool = await get_arq_pool()
        redis = await get_redis()

        fixed_id = job_id is not None
        job_id = job_id or str(uuid.uuid4())

        use_sub_queue = depth > 0 and settings.agent_subagent_queue
        queue_label = "agents:sub" if use_sub_queue else "agents"

        status_data: dict[str, Any] = {
            "status": "queued",
            "channel_id": channel_id,
            "queue": queue_label,
            "type": "message",
            "chat_id": chat_id,
            "enqueued_at": time.time(),
        }
        if agent:
            status_data["agent"] = agent
        if trigger_type:
            status_data["trigger_type"] = trigger_type

        await redis.set(
            f"{JOB_STATUS_PREFIX}{job_id}",
            json.dumps(status_data),
            ex=JOB_TTL,
            # A retried enqueue must not reset the status of the job it repeats.
            nx=fixed_id,
        )

        enqueue_kwargs: dict[str, Any] = {
            "_job_id": job_id,
            "_queue_name": SUB_AGENT_QUEUE if use_sub_queue else AGENT_QUEUE,
        }

        await pool.enqueue_job(
            "execute_agent_message_job",
            job_id=job_id,
            chat_id=chat_id,
            user_id=user_id,
            user_token=user_token,
            content=content,
            channel_id=channel_id,
            execution_id=execution_id,
            depth=depth,
            pending_delegation_id=pending_delegation_id,
            parent_tool_call_id=parent_tool_call_id,
            trace_context=inject_job_context(),
            # The API key the run acts through: its tools check the key's
            # permissions, not the owner's (core/auth get_effective_permissions).
            api_key_id=current_api_key_id(),
            # And its attribution context (the job binds it, _acting_as_job_key).
            credential_context=current_credential_context(),
            # The job enforces it (agent_jobs._within_job_timeout): arq's
            # enqueue_job takes no per-job timeout.
            job_timeout=job_timeout,
            **enqueue_kwargs,
        )

        logger.info(
            f"Enqueued agent message job {job_id} for chat {chat_id} "
            f"(queue={queue_label}, depth={depth})"
        )
        return job_id

    async def enqueue_agent_resume(
        self,
        chat_id: str,
        user_id: str,
        user_token: str,
        pending_approval_id: str,
        approved: bool,
        channel_id: str,
        agent: Optional[str] = None,
        job_timeout: Optional[int] = None,
        defer_seconds: Optional[int] = None,
        attempt: int = 0,
        enqueued_at: Optional[float] = None,
        rejected_tool_call_ids: Optional[list[str]] = None,
        job_id: Optional[str] = None,
    ) -> str:
        """Enqueue an agent resume job (after tool approval).

        `rejected_tool_call_ids`: the round's asked calls the user said no
        to; the resumed round answers them with a rejection instead of
        running them. `job_id`: a fixed id makes the enqueue idempotent —
        a round resumed by the approve endpoint and by the expiry sweep
        runs once (arq won't queue a second job under an id it has).

        `defer_seconds`/`attempt`: a retry of a continuation that found the
        chat busy. `enqueued_at` is kept from the first enqueue so an
        operator interrupt requested after it still stops the retry.
        """
        pool = await get_arq_pool()
        redis = await get_redis()

        fixed_id = job_id is not None
        job_id = job_id or str(uuid.uuid4())

        status_data: dict[str, Any] = {
            "status": "queued",
            "channel_id": channel_id,
            "queue": "agents",
            "type": "resume",
            "chat_id": chat_id,
            "enqueued_at": time.time(),
        }
        if agent:
            status_data["agent"] = agent

        await redis.set(
            f"{JOB_STATUS_PREFIX}{job_id}",
            json.dumps(status_data),
            ex=JOB_TTL,
            nx=fixed_id,
        )

        enqueue_kwargs: dict[str, Any] = {
            "_job_id": job_id,
            "_queue_name": "sinas:queue:agents",
        }
        if defer_seconds:
            enqueue_kwargs["_defer_by"] = timedelta(seconds=defer_seconds)

        await pool.enqueue_job(
            "execute_agent_resume_job",
            job_id=job_id,
            chat_id=chat_id,
            user_id=user_id,
            user_token=user_token,
            pending_approval_id=pending_approval_id,
            approved=approved,
            rejected_tool_call_ids=list(rejected_tool_call_ids or []),
            channel_id=channel_id,
            trace_context=inject_job_context(),
            # The API key the run acts through: its tools check the key's
            # permissions, not the owner's (core/auth get_effective_permissions).
            api_key_id=current_api_key_id(),
            # And its attribution context (the job binds it, _acting_as_job_key).
            credential_context=current_credential_context(),
            attempt=attempt,
            enqueued_at=enqueued_at if enqueued_at is not None else time.time(),
            # The job enforces it (agent_jobs._within_job_timeout): arq's
            # enqueue_job takes no per-job timeout.
            job_timeout=job_timeout,
            **enqueue_kwargs,
        )

        logger.info(f"Enqueued agent resume job {job_id} for chat {chat_id}")
        return job_id

    async def enqueue_agent_delegate_resume(
        self,
        chat_id: str,
        user_id: str,
        user_token: str,
        channel_id: str,
        conversation_context: dict[str, Any],
        job_timeout: Optional[int] = None,
        job_id: Optional[str] = None,
        defer_seconds: Optional[int] = None,
        attempt: int = 0,
        enqueued_at: Optional[float] = None,
    ) -> str:
        """Enqueue continuation of a parent suspended on delegations (issue #90).

        `job_id`: a fixed id makes the enqueue idempotent (arq won't queue a
        second job under an id it already has, and a status already recorded
        for it — possibly "completed" by now — is left as it is).

        Fired by the last finishing child. Routed by the parent's own
        delegation depth, so a suspended sub-agent resumes on the sub-agent
        queue and a top-level parent on the main queue.
        """
        pool = await get_arq_pool()
        redis = await get_redis()

        fixed_id = job_id is not None
        job_id = job_id or str(uuid.uuid4())
        depth = conversation_context.get("delegation_depth", 0)
        use_sub_queue = depth > 0 and settings.agent_subagent_queue
        queue_label = "agents:sub" if use_sub_queue else "agents"

        status_data: dict[str, Any] = {
            "status": "queued",
            "channel_id": channel_id,
            "queue": queue_label,
            "type": "delegate_resume",
            "chat_id": chat_id,
            "enqueued_at": time.time(),
        }
        await redis.set(
            f"{JOB_STATUS_PREFIX}{job_id}",
            json.dumps(status_data),
            ex=JOB_TTL,
            nx=fixed_id,
        )

        enqueue_kwargs: dict[str, Any] = {
            "_job_id": job_id,
            "_queue_name": SUB_AGENT_QUEUE if use_sub_queue else AGENT_QUEUE,
        }
        if defer_seconds:
            enqueue_kwargs["_defer_by"] = timedelta(seconds=defer_seconds)

        await pool.enqueue_job(
            "execute_agent_delegate_resume_job",
            job_id=job_id,
            chat_id=chat_id,
            user_id=user_id,
            user_token=user_token,
            channel_id=channel_id,
            conversation_context=conversation_context,
            trace_context=inject_job_context(),
            # The API key the run acts through: its tools check the key's
            # permissions, not the owner's (core/auth get_effective_permissions).
            api_key_id=current_api_key_id(),
            # And its attribution context (the job binds it, _acting_as_job_key).
            credential_context=current_credential_context(),
            attempt=attempt,
            enqueued_at=enqueued_at if enqueued_at is not None else time.time(),
            # The job enforces it (agent_jobs._within_job_timeout): arq's
            # enqueue_job takes no per-job timeout.
            job_timeout=job_timeout,
            **enqueue_kwargs,
        )

        logger.info(
            f"Enqueued delegate-resume job {job_id} for chat {chat_id} (queue={queue_label})"
        )
        return job_id


    async def get_queue_stats(self) -> dict[str, Any]:
        """Get aggregate queue statistics."""
        redis = await get_redis()

        # Queue depths (arq uses sorted sets)
        functions_pending = await redis.zcard("sinas:queue:functions")
        agents_pending = await redis.zcard(AGENT_QUEUE)
        sub_agents_pending = await redis.zcard(SUB_AGENT_QUEUE)

        # DLQ size, and how many entries its cap dropped
        dlq_size = await redis.llen(DLQ_KEY)
        dlq_dropped = int(await redis.get(DLQ_DROPPED_KEY) or 0)

        # Job status counts via SCAN
        status_counts: dict[str, int] = {}
        cursor = 0
        while True:
            cursor, keys = await redis.scan(
                cursor, match=f"{JOB_STATUS_PREFIX}*", count=200
            )
            if keys:
                values = await redis.mget(*keys)
                for val in values:
                    if val:
                        try:
                            status = json.loads(val).get("status", "unknown")
                            status_counts[status] = status_counts.get(status, 0) + 1
                        except (json.JSONDecodeError, TypeError):
                            pass
            if cursor == 0:
                break

        return {
            "queues": {
                "functions": {"pending": functions_pending},
                "agents": {"pending": agents_pending},
                "agents:sub": {"pending": sub_agents_pending},
            },
            "jobs": {
                "queued": status_counts.get("queued", 0),
                "running": status_counts.get("running", 0),
                "completed": status_counts.get("completed", 0),
                "failed": status_counts.get("failed", 0),
                "cancelled": status_counts.get("cancelled", 0),
            },
            "dlq": {
                "size": dlq_size,
                "dropped": dlq_dropped,
                "max_entries": settings.queue_dlq_max_entries,
            },
        }

    async def get_jobs_list(
        self,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List jobs, optionally filtered by status. Sorted newest first."""
        redis = await get_redis()
        jobs: list[dict[str, Any]] = []

        cursor = 0
        while True:
            cursor, keys = await redis.scan(
                cursor, match=f"{JOB_STATUS_PREFIX}*", count=200
            )
            if keys:
                values = await redis.mget(*keys)
                for key, val in zip(keys, values):
                    if val:
                        try:
                            data = json.loads(val)
                        except (json.JSONDecodeError, TypeError):
                            continue
                        if status and data.get("status") != status:
                            continue
                        job_id = key.removeprefix(JOB_STATUS_PREFIX)
                        jobs.append({
                            "job_id": job_id,
                            "status": data.get("status"),
                            "queue": data.get("queue"),
                            "function": data.get("function"),
                            "agent": data.get("agent"),
                            "type": data.get("type"),
                            "trigger_type": data.get("trigger_type"),
                            "chat_id": data.get("chat_id"),
                            "execution_id": data.get("execution_id"),
                            "channel_id": data.get("channel_id"),
                            "error": data.get("error"),
                            "enqueued_at": data.get("enqueued_at"),
                        })
            if cursor == 0:
                break

        # Sort newest first; jobs without timestamp sort last
        jobs.sort(key=lambda j: j.get("enqueued_at") or 0, reverse=True)
        return jobs[:limit]

    async def get_dlq_entries(self) -> list[dict[str, Any]]:
        """Get all dead-letter queue entries."""
        redis = await get_redis()
        raw_entries = await redis.lrange(DLQ_KEY, 0, -1)
        entries: list[dict[str, Any]] = []
        for raw in raw_entries:
            try:
                entry = json.loads(raw)
                entries.append({
                    "job_id": entry.get("job_id"),
                    "function": entry.get("function"),
                    "execution_id": entry.get("execution_id"),
                    "trigger_type": entry.get("trigger_type"),
                    "error": entry.get("error"),
                    # infrastructure | function | saturation (absent on
                    # entries written before issue #135)
                    "error_type": entry.get("error_type"),
                    "attempts": entry.get("attempts"),
                    "failed_at": entry.get("failed_at"),
                })
            except (json.JSONDecodeError, TypeError):
                pass
        return entries

    async def retry_dlq_job(self, job_id: str) -> dict[str, Any]:
        """Re-enqueue a dead-lettered job and remove it from the DLQ.

        The job id is the execution id, so the requeued job reuses it: its
        failure count starts over and its execution goes back to PENDING (a
        finished batch it belongs to is reopened). One requeue per job at a
        time; while arq still has the job queued or running, nothing is
        touched (RuntimeError, 409)."""
        redis = await get_redis()
        lock_key = f"{DLQ_REQUEUE_LOCK_PREFIX}{job_id}"
        if not await redis.set(lock_key, "1", nx=True, ex=60):
            raise RuntimeError(f"Job {job_id} is already being requeued")
        try:
            return await self._requeue_locked(redis, job_id)
        finally:
            await redis.delete(lock_key)

    async def _requeue_locked(self, redis, job_id: str) -> dict[str, Any]:
        raw_entries = await redis.lrange(DLQ_KEY, 0, -1)

        target_entry = None
        target_raw = None
        for raw in raw_entries:
            try:
                entry = json.loads(raw)
                if entry.get("job_id") == job_id:
                    target_entry = entry
                    target_raw = raw
                    break
            except (json.JSONDecodeError, TypeError):
                continue

        if not target_entry:
            raise ValueError(f"Job {job_id} not found in DLQ")

        # Verify we have the parameters needed for re-enqueue
        required = ["function_namespace", "function_name", "input_data",
                     "execution_id", "trigger_type", "trigger_id", "user_id"]
        missing = [k for k in required if k not in target_entry]
        if missing:
            raise ValueError(
                f"DLQ entry missing fields for retry: {missing}. "
                "Job was enqueued before retry support was added."
            )

        execution_id = target_entry["execution_id"]

        from arq.constants import in_progress_key_prefix, job_key_prefix, result_key_prefix

        if await redis.exists(f"{job_key_prefix}{execution_id}", f"{in_progress_key_prefix}{execution_id}"):
            raise RuntimeError(f"Function job {execution_id} is already queued or running")

        old_status = await redis.get(f"{JOB_STATUS_PREFIX}{execution_id}")
        snapshot = await self._reset_execution_for_requeue(execution_id)
        # Enqueue under the original request's attribution context, not the
        # requeueing admin's (enqueue_function reads it from the context).
        from app.core.auth import bind_credential_context, reset_credential_context

        context_token = bind_credential_context(target_entry.get("credential_context"))
        try:
            # arq keeps a failed job's result for an hour and refuses to
            # enqueue an id that still has one.
            await redis.delete(
                f"{result_key_prefix}{execution_id}", f"{JOB_FAILURES_PREFIX}{execution_id}"
            )
            new_job_id = await self.enqueue_function(
                function_namespace=target_entry["function_namespace"],
                function_name=target_entry["function_name"],
                input_data=target_entry["input_data"],
                execution_id=execution_id,
                trigger_type=target_entry["trigger_type"],
                trigger_id=target_entry["trigger_id"],
                user_id=target_entry["user_id"],
                chat_id=target_entry.get("chat_id"),
                callback_url=target_entry.get("callback_url"),
                depth=target_entry.get("depth") or 0,
            )
        except Exception:
            # Not queued: put the execution, its batch and the job status
            # back as they were, so nothing claims a run that never comes.
            # The entry stays in the DLQ for another try.
            await self._restore_after_failed_requeue(execution_id, snapshot)
            try:
                if old_status is not None:
                    await redis.set(f"{JOB_STATUS_PREFIX}{execution_id}", old_status, ex=JOB_TTL)
                else:
                    # The old key had expired (DLQ entries outlive it): drop
                    # the "queued" one enqueue_function wrote.
                    await redis.delete(f"{JOB_STATUS_PREFIX}{execution_id}")
            except Exception:
                logger.warning(f"Could not restore the job status of {execution_id}", exc_info=True)
            raise
        finally:
            reset_credential_context(context_token)

        # Only once it is queued again: a failed requeue keeps the entry.
        await redis.lrem(DLQ_KEY, 1, target_raw)

        logger.info(f"Requeued DLQ job {job_id} as job {new_job_id}")
        return {"old_job_id": job_id, "new_job_id": new_job_id}

    async def _reset_execution_for_requeue(self, execution_id: str) -> Optional[dict[str, Any]]:
        """Put a dead-lettered execution back to PENDING, so its record shows
        it queued again rather than failed, and reopen a finished batch it
        belongs to (its terminal status and callback follow the new outcome).
        Refuses (RuntimeError) an execution that completed or is waiting for
        input, or whose batch was cancelled.

        Returns what it changed, for `_restore_after_failed_requeue` (None:
        there was no row). The batch is locked as `on_execution_terminated`
        locks it, so a late child termination can't re-finish the reopened
        batch from stale counts."""
        from sqlalchemy import select

        from app.core.database import AsyncSessionLocal
        from app.models.batch import Batch, BatchStatus
        from app.models.execution import Execution, ExecutionStatus

        async with AsyncSessionLocal() as db:
            execution = (
                await db.execute(
                    select(Execution)
                    .where(Execution.execution_id == execution_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if execution is None:
                return None  # the engine creates it when the job runs
            if execution.status in (ExecutionStatus.COMPLETED, ExecutionStatus.AWAITING_INPUT):
                raise RuntimeError(
                    f"Execution {execution_id} is {execution.status.value}; not requeueing it"
                )
            snapshot: dict[str, Any] = {
                "execution": {
                    field: getattr(execution, field)
                    for field in (
                        "status", "error", "traceback", "output_data", "completed_at", "duration_ms",
                    )
                },
                "batch_id": execution.batch_id,
            }
            if execution.batch_id is not None:
                batch = (
                    await db.execute(
                        select(Batch).where(Batch.id == execution.batch_id).with_for_update()
                    )
                ).scalar_one_or_none()
                if batch is not None:
                    if batch.status == BatchStatus.CANCELLED:
                        raise RuntimeError(
                            f"Execution {execution_id} belongs to cancelled batch {batch.id}"
                        )
                    if batch.finished_at is not None:
                        snapshot["batch"] = {
                            "status": batch.status,
                            "finished_at": batch.finished_at,
                        }
                        batch.status = BatchStatus.RUNNING
                        batch.finished_at = None
            execution.status = ExecutionStatus.PENDING
            execution.error = None
            execution.traceback = None
            execution.output_data = None
            execution.completed_at = None
            execution.duration_ms = None
            await db.commit()
            return snapshot

    async def _restore_after_failed_requeue(
        self, execution_id: str, snapshot: Optional[dict[str, Any]]
    ) -> None:
        """Undo `_reset_execution_for_requeue` after the enqueue failed.
        Best effort: never raises."""
        if snapshot is None:
            return
        from sqlalchemy import select

        from app.core.database import AsyncSessionLocal
        from app.models.execution import Execution, ExecutionStatus

        try:
            # Same lock order as the reset: execution, then batch.
            async with AsyncSessionLocal() as db:
                execution = (
                    await db.execute(
                        select(Execution)
                        .where(Execution.execution_id == execution_id)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if execution is not None and execution.status == ExecutionStatus.PENDING:
                    for field, value in snapshot["execution"].items():
                        setattr(execution, field, value)
                await db.commit()

                if snapshot.get("batch") and snapshot.get("batch_id") is not None:
                    await self._refinish_batch(db, snapshot["batch_id"], snapshot["batch"])
        except Exception:
            logger.error(f"Could not restore execution {execution_id} after a failed requeue", exc_info=True)

    async def _refinish_batch(self, db, batch_id, before: dict[str, Any]) -> None:
        """Close a batch the failed requeue had reopened — judged by its
        children now, under the batch lock, since other requeues may have
        changed them meanwhile: still a child in flight → stays open (its
        termination finishes the batch); the same outcome as before → the
        old status and finished_at come back, no second callback; a
        different outcome → finished afresh, callback included."""
        from sqlalchemy import select

        from app.models.batch import Batch
        from app.services import batch_service

        batch = (
            await db.execute(select(Batch).where(Batch.id == batch_id).with_for_update())
        ).scalar_one_or_none()
        if batch is None or batch.finished_at is not None:
            await db.commit()
            return
        if await batch_service._count_pending(db, batch_id) > 0:
            await db.commit()
            return
        counts = await batch_service._count_by_status(db, batch_id)
        if batch_service._derive_terminal_status(counts) == before["status"]:
            batch.status = before["status"]
            batch.finished_at = before["finished_at"]
            await db.commit()
            return
        await db.commit()  # release the lock; on_execution_terminated takes it
        await batch_service.on_execution_terminated(db=db, batch_id=batch_id)

    async def cancel_job(self, job_id: str) -> dict[str, Any]:
        """
        Cancel a job by updating its Redis status and DB execution record.

        This is a soft cancel — it marks the job as cancelled but does not
        kill running containers or abort arq tasks. Useful for cleaning up
        orphaned/stale jobs.
        """
        redis = await get_redis()

        # Read existing status to preserve metadata fields
        existing = await redis.get(f"{JOB_STATUS_PREFIX}{job_id}")
        if not existing:
            raise ValueError(f"Job {job_id} not found")

        try:
            status_data = json.loads(existing)
        except (json.JSONDecodeError, TypeError):
            status_data = {}

        status_data["status"] = "cancelled"
        status_data["cancelled_at"] = time.time()

        await redis.set(
            f"{JOB_STATUS_PREFIX}{job_id}",
            json.dumps(status_data),
            ex=JOB_TTL,
        )

        # Remove any stale result
        await redis.delete(f"{JOB_RESULT_PREFIX}{job_id}")

        # Publish to done channel so any waiters unblock
        execution_id = status_data.get("execution_id", job_id)
        await redis.publish(
            f"{JOB_DONE_CHANNEL_PREFIX}{execution_id}",
            json.dumps({"status": "cancelled", "job_id": job_id}),
        )

        # Update DB execution record if it exists
        try:
            from app.core.database import AsyncSessionLocal
            from app.models.execution import Execution, ExecutionStatus
            from sqlalchemy import select

            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(Execution).where(Execution.execution_id == execution_id)
                )
                execution = result.scalar_one_or_none()
                if execution:
                    execution.status = ExecutionStatus.CANCELLED
                    await db.commit()
        except Exception:
            logger.warning(f"Could not update DB execution for {execution_id}", exc_info=True)

        logger.info(f"Cancelled job {job_id}")
        return {"status": "cancelled", "job_id": job_id}

    async def flush_dlq(self) -> dict[str, Any]:
        """Remove all entries from the dead letter queue (and reset the count
        of entries its cap dropped)."""
        redis = await get_redis()
        size = await redis.llen(DLQ_KEY)
        await redis.delete(DLQ_KEY, DLQ_DROPPED_KEY)
        logger.info(f"Flushed {size} DLQ entries")
        return {"flushed": size}

    async def flush_stuck_jobs(self, max_age_hours: int = 2) -> dict[str, Any]:
        """Cancel jobs stuck in 'running' state for over max_age_hours."""
        redis = await get_redis()
        cutoff = time.time() - (max_age_hours * 3600)
        cancelled = 0

        cursor = 0
        while True:
            cursor, keys = await redis.scan(
                cursor, match=f"{JOB_STATUS_PREFIX}*", count=100
            )
            if keys:
                values = await redis.mget(*keys)
                for key, val in zip(keys, values):
                    if not val:
                        continue
                    try:
                        data = json.loads(val)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if data.get("status") != "running":
                        continue
                    enqueued = data.get("enqueued_at", 0)
                    if enqueued and enqueued < cutoff:
                        # The shared client decodes responses (str keys);
                        # bytes only from a client that doesn't.
                        if isinstance(key, bytes):
                            key = key.decode()
                        job_id = data.get("job_id") or key.removeprefix(JOB_STATUS_PREFIX)
                        try:
                            await self.cancel_job(job_id)
                            cancelled += 1
                        except Exception:
                            logger.warning(f"Could not cancel stuck job {job_id}", exc_info=True)
            if cursor == 0:
                break

        logger.info(f"Flushed {cancelled} stuck jobs (>{max_age_hours}h)")
        return {"cancelled": cancelled, "max_age_hours": max_age_hours}

    async def get_active_workers(self) -> list[dict[str, Any]]:
        """List active arq worker processes from heartbeat keys."""
        from app.queue.worker import WORKER_HEARTBEAT_PREFIX

        redis = await get_redis()
        workers: list[dict[str, Any]] = []

        cursor = 0
        while True:
            cursor, keys = await redis.scan(
                cursor, match=f"{WORKER_HEARTBEAT_PREFIX}*", count=100
            )
            if keys:
                values = await redis.mget(*keys)
                for val in values:
                    if val:
                        try:
                            workers.append(json.loads(val))
                        except (json.JSONDecodeError, TypeError):
                            pass
            if cursor == 0:
                break

        # Sort by queue then start time
        workers.sort(key=lambda w: (w.get("queue", ""), w.get("started_at", 0)))
        return workers


# Global instance
queue_service = QueueService()
