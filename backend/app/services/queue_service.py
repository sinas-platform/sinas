"""Queue service for dispatching function and agent jobs via arq."""
import asyncio
import json
import logging
import time
import uuid
from typing import Any, Optional

from app.core.config import settings
from app.core.redis import get_arq_pool, get_redis

logger = logging.getLogger(__name__)

# Redis key prefixes
JOB_STATUS_PREFIX = "sinas:job:status:"
JOB_RESULT_PREFIX = "sinas:job:result:"
JOB_DONE_CHANNEL_PREFIX = "sinas:job:done:"
DLQ_KEY = "sinas:queue:dlq"
JOB_TTL = 86400  # 24 hours


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
        resume_data: Optional[dict[str, Any]] = None,
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
        }
        if resume_data is not None:
            job_kwargs["resume_data"] = resume_data

        # Enqueue with optional delay
        enqueue_kwargs = {"_job_id": job_id, "_queue_name": "sinas:queue:functions"}
        if delay:
            enqueue_kwargs["_defer_by"] = delay

        await pool.enqueue_job(
            "execute_function_job",
            **job_kwargs,
            **enqueue_kwargs,
        )

        logger.info(
            f"Enqueued function job: {function_namespace}/{function_name} "
            f"(execution_id={execution_id})"
        )
        return execution_id

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
        resume_data: Optional[dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> Any:
        """
        Enqueue a function job and wait for its result.

        Uses Redis pub/sub to get notified when the job completes.
        """
        redis = await get_redis()
        timeout = timeout or settings.queue_default_timeout

        job_id = await self.enqueue_function(
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

        # Subscribe to completion channel
        channel = f"{JOB_DONE_CHANNEL_PREFIX}{execution_id}"
        pubsub = redis.pubsub()
        await pubsub.subscribe(channel)

        try:
            # Poll for result with timeout
            deadline = asyncio.get_event_loop().time() + timeout
            while asyncio.get_event_loop().time() < deadline:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg and msg["type"] == "message":
                    result_data = json.loads(msg["data"])
                    if result_data.get("status") == "failed":
                        raise Exception(result_data.get("error", "Job failed"))
                    return result_data.get("result")

                # Also check if result is already stored (race condition safety)
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
    ) -> str:
        """Enqueue an agent message processing job."""
        pool = await get_arq_pool()
        redis = await get_redis()

        job_id = str(uuid.uuid4())

        status_data: dict[str, Any] = {
            "status": "queued",
            "channel_id": channel_id,
            "queue": "agents",
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
        )

        await pool.enqueue_job(
            "execute_agent_message_job",
            job_id=job_id,
            chat_id=chat_id,
            user_id=user_id,
            user_token=user_token,
            content=content,
            channel_id=channel_id,
            _job_id=job_id,
            _queue_name="sinas:queue:agents",
        )

        logger.info(f"Enqueued agent message job {job_id} for chat {chat_id}")
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
    ) -> str:
        """Enqueue an agent resume job (after tool approval)."""
        pool = await get_arq_pool()
        redis = await get_redis()

        job_id = str(uuid.uuid4())

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
        )

        await pool.enqueue_job(
            "execute_agent_resume_job",
            job_id=job_id,
            chat_id=chat_id,
            user_id=user_id,
            user_token=user_token,
            pending_approval_id=pending_approval_id,
            approved=approved,
            channel_id=channel_id,
            _job_id=job_id,
            _queue_name="sinas:queue:agents",
        )

        logger.info(f"Enqueued agent resume job {job_id} for chat {chat_id}")
        return job_id


    async def get_queue_stats(self) -> dict[str, Any]:
        """Get aggregate queue statistics."""
        redis = await get_redis()

        # Queue depths (arq uses sorted sets)
        functions_pending = await redis.zcard("sinas:queue:functions")
        agents_pending = await redis.zcard("sinas:queue:agents")

        # DLQ size
        dlq_size = await redis.llen(DLQ_KEY)

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
            },
            "jobs": {
                "queued": status_counts.get("queued", 0),
                "running": status_counts.get("running", 0),
                "completed": status_counts.get("completed", 0),
                "failed": status_counts.get("failed", 0),
                "cancelled": status_counts.get("cancelled", 0),
            },
            "dlq": {"size": dlq_size},
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
                    "error": entry.get("error"),
                    "attempts": entry.get("attempts"),
                })
            except (json.JSONDecodeError, TypeError):
                pass
        return entries

    async def retry_dlq_job(self, job_id: str) -> dict[str, Any]:
        """Remove a job from the DLQ and re-enqueue it."""
        redis = await get_redis()
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

        # Remove from DLQ
        await redis.lrem(DLQ_KEY, 1, target_raw)

        # Re-enqueue with a new job ID
        new_job_id = await self.enqueue_function(
            function_namespace=target_entry["function_namespace"],
            function_name=target_entry["function_name"],
            input_data=target_entry["input_data"],
            execution_id=target_entry["execution_id"],
            trigger_type=target_entry["trigger_type"],
            trigger_id=target_entry["trigger_id"],
            user_id=target_entry["user_id"],
            chat_id=target_entry.get("chat_id"),
        )

        logger.info(f"Retried DLQ job {job_id} as new job {new_job_id}")
        return {"old_job_id": job_id, "new_job_id": new_job_id}

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
