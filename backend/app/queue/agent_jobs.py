"""arq job handlers for agent message processing."""
import asyncio
import functools
import json
import logging
import traceback
import uuid as uuid_lib
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any, Optional

from opentelemetry import trace
from sqlalchemy import select

from app.core.config import settings
from app.core.redis import is_redis_oom, redis_oom_summary
from app.core.telemetry import attached, extract_trace_context, otel_attr, traced_job
from app.models.execution import Execution, ExecutionStatus
from app.providers.tracking import collect_usage
from app.services.deferred_completions import ROUND_SUSPENDED
from app.services.queue_service import JOB_STATUS_PREFIX, JOB_TTL

logger = logging.getLogger(__name__)

PING_INTERVAL = 15  # seconds between keep-alive pings


def _failure_fields(error: BaseException) -> dict[str, Any]:
    """A failed job's error for its status record and its parent's report:
    `error`, and the error envelope's `error_code`, `retryable` and (when
    the model API answered) `upstream_status` (app/providers/errors.py)."""
    from app.providers.errors import failure_fields

    return failure_fields(error)


# What a job that was cancelled, or ran out of its job_timeout, reports.
_TIMED_OUT = "Job cancelled or timed out"


def _timed_out_error():
    from app.providers.errors import JOB_TIMEOUT, LLMError

    return LLMError(JOB_TIMEOUT, _TIMED_OUT)


def _acting_as_job_key(job):
    """Run an agent job through the API key its request came in on (if any):
    its tools then check the key's permissions, as in the request itself.
    The job also carries that request's attribution context (chats, pipeline
    runs and component pages it starts are attributed as the request's
    would be): the one it was enqueued with, else the key's own."""
    import functools

    from app.core.auth import (
        bind_api_key,
        bind_credential_context,
        reset_api_key,
        reset_credential_context,
    )

    @functools.wraps(job)
    async def wrapper(ctx: dict, **kwargs: Any) -> None:
        token = bind_api_key(kwargs.get("api_key_id"))
        context = kwargs.get("credential_context")
        if context is None and kwargs.get("api_key_id"):
            context = await _stored_key_context(kwargs["api_key_id"])
        context_token = bind_credential_context(context)
        try:
            return await job(ctx, **kwargs)
        finally:
            reset_credential_context(context_token)
            reset_api_key(token)

    return wrapper


async def _stored_key_context(api_key_id: str) -> Optional[dict[str, Any]]:
    """An API key's stored attribution context, for a job enqueued without
    one (a wakeup, a watch, an MCP task delivered from a stored key id)."""
    from app.core.database import AsyncSessionLocal
    from app.models.user import APIKey

    try:
        async with AsyncSessionLocal() as db:
            key = await db.get(APIKey, uuid_lib.UUID(str(api_key_id)))
            return key.context if key is not None else None
    except Exception as e:
        logger.warning(f"Could not read the context of API key {api_key_id}: {e}")
        return None


def agent_job_timeout_ceiling() -> int:
    """The longest any one agent job may run (AGENT_JOB_TIMEOUT_MAX, never
    below AGENT_JOB_TIMEOUT)."""
    return max(settings.agent_job_timeout_max, settings.agent_job_timeout)


def agent_job_timeout(requested: Optional[int]) -> int:
    """The timeout one agent job runs under: the job_timeout it was queued
    with (the chat's, else its agent's), else AGENT_JOB_TIMEOUT; capped at
    the ceiling."""
    seconds = requested if requested and requested > 0 else settings.agent_job_timeout
    return min(seconds, agent_job_timeout_ceiling())


def agent_function_timeout() -> int:
    """arq's own timeout for the agent job functions (func(..., timeout=)):
    past the longest per-job timeout, so the job's own (below) always fires
    first and its cleanup runs before arq would cancel it."""
    return agent_job_timeout_ceiling() + 60


def _within_job_timeout(
    job: Callable[..., Awaitable[None]],
) -> Callable[..., Awaitable[None]]:
    """Enforce the job's own `job_timeout`. arq has no per-job timeout (its
    enqueue_job takes none; an unknown `_job_timeout` just reaches the job
    as an argument), only the function's, so the job applies it: on expiry
    the job is cancelled exactly as arq's timeout would cancel it (its
    cleanup reports "cancelled or timed out") and TimeoutError fails it."""

    @functools.wraps(job)
    async def run(ctx: dict[str, Any], **kwargs: Any) -> Any:
        from app.services import chat_steering

        seconds = agent_job_timeout(kwargs.get("job_timeout"))
        # The chat lock the job takes lives as long as the job may run.
        token = chat_steering.current_job_timeout.set(seconds)
        try:
            # LLM call retries don't wait past it (app/services/llm_retry.py).
            with chat_steering.job_deadline(seconds):
                async with asyncio.timeout(seconds):
                    return await job(ctx, **kwargs)
        finally:
            chat_steering.current_job_timeout.reset(token)

    return run


def _is_suspension_chunk(chunk: dict) -> bool:
    """A chunk announcing the tool round suspended on pending completions
    (delegations, ask_user questions). The job must then end WITHOUT a
    "done" event or terminal bookkeeping — a resume job owns the rest of
    the conversation."""
    from app.services.deferred_completions import SUSPENSION_EVENT_TYPES

    return chunk.get("type") in SUSPENSION_EVENT_TYPES


async def _usage_totals(collector) -> Optional[dict[str, Any]]:
    """The LLM usage a job collected, or None (logged) if it can't be totalled."""
    try:
        return await collector.totals()
    except Exception:
        logger.exception("Failed to total the LLM usage of this job")
        return None


def _in_enqueued_context(
    job: Callable[..., Awaitable[None]],
) -> Callable[..., Awaitable[None]]:
    """Run a queued job inside the trace context it was enqueued with.

    The enqueue side passes `trace_context` (`inject_trace_context`); making
    it current for the whole job means the model calls a continuation makes
    carry the caller's trace and baggage, as the turn it continues did.
    """

    @functools.wraps(job)
    async def run(ctx: dict[str, Any], **kwargs: Any) -> None:
        with attached(extract_trace_context(kwargs.get("trace_context") or {})):
            await job(ctx, **kwargs)

    return run


async def _mark_running(
    ctx: dict,
    redis: Any,
    job_id: str,
    base_fields: dict[str, Any],
    *,
    chat_id: str,
    user_token: str,
    execution_id: Optional[str] = None,
    parent_delegation_id: Optional[str] = None,
    parent_tool_call_id: Optional[str] = None,
) -> None:
    """The job's first write: its "running" status. Refused because Redis is
    full, the turn has not started, so the job is deferred (and may run
    again despite max_tries=1) instead of failing: arq would finish it, and
    on a full Redis drop it, without a trace in the chat. Past
    QUEUE_REDIS_FULL_MAX_WAIT_SECONDS the turn ends with a recorded error
    instead (`_turn_never_started`)."""
    try:
        await redis.set(
            f"{JOB_STATUS_PREFIX}{job_id}",
            json.dumps({**base_fields, "status": "running"}),
            ex=JOB_TTL,
        )
    except Exception as e:
        if not is_redis_oom(e):
            raise
        from app.queue.arq_worker import RedisFullTooLong, defer_job_redis_full

        try:
            await defer_job_redis_full(
                redis, ctx.get("job_id") or job_id, f"Agent job {job_id}",
                enqueued_at=ctx.get("enqueue_time"),
            )
        except RedisFullTooLong as too_long:
            await _turn_never_started(
                too_long, chat_id=chat_id, user_token=user_token, execution_id=execution_id,
                parent_delegation_id=parent_delegation_id, parent_tool_call_id=parent_tool_call_id,
            )
            raise


async def _turn_never_started(
    error: Exception,
    *,
    chat_id: str,
    user_token: str,
    execution_id: Optional[str],
    parent_delegation_id: Optional[str],
    parent_tool_call_id: Optional[str],
) -> None:
    """Record a turn that gave up waiting for Redis, where Redis isn't
    needed: the error in the chat, the batch Execution row, and the
    suspended parent this turn was a delegation of (each best effort)."""
    await _persist_turn_error(chat_id, error)
    if execution_id:
        try:
            await _terminate_execution_row(execution_id, ExecutionStatus.FAILED, error=str(error))
        except Exception:
            logger.exception(f"Could not fail execution {execution_id}")
    if parent_delegation_id and parent_tool_call_id:
        from app.services.delegation import on_child_complete

        try:
            await on_child_complete(
                parent_delegation_id,
                parent_tool_call_id,
                json.dumps({**_failure_fields(error), "chat_id": chat_id}),
                user_token=user_token,
            )
        except Exception:
            logger.exception(f"Could not report to pending delegation {parent_delegation_id}")


async def _unless_redis_full(write: Awaitable[Any], what: str) -> None:
    """Await a Redis write on a failure path; when Redis is full, log it and
    go on, so the rest of the failure bookkeeping (the chat's error message,
    the Execution row, the parent delegation) still happens."""
    try:
        await write
    except Exception as e:
        if not is_redis_oom(e):
            raise
        logger.error(f"{what} not written: Redis is full ({redis_oom_summary(e)})")


async def _ping_loop(channel_id: str, ttl: int | None = None) -> None:
    """Background task that publishes ping events to keep SSE connections alive."""
    from app.services.stream_relay import stream_relay

    while True:
        await asyncio.sleep(PING_INTERVAL)
        try:
            await stream_relay.publish(channel_id, {"type": "ping"}, ttl=ttl)
        except Exception:
            pass  # Best-effort; don't let ping failures kill the loop


async def _terminate_execution_row(
    execution_id: str,
    status: ExecutionStatus,
    *,
    output: Optional[dict[str, Any]] = None,
    error: Optional[str] = None,
) -> None:
    """Mark a batch-child Execution terminal and fire batch-completion check.

    Used by batch-initiated agent runs (where queue_service hands us an
    execution_id). Non-batch agent runs pass no execution_id and this is a no-op.
    """
    from app.core.database import AsyncSessionLocal
    from app.services import batch_service

    async with AsyncSessionLocal() as db:
        row = await db.execute(
            select(Execution).where(Execution.execution_id == execution_id)
        )
        execution = row.scalar_one_or_none()
        if not execution:
            logger.warning("Execution %s not found when terminating agent batch child", execution_id)
            return
        execution.status = status
        execution.completed_at = datetime.now(timezone.utc)
        if output is not None:
            execution.output_data = output
        if error is not None:
            execution.error = error
        await db.commit()

        if execution.batch_id is not None:
            await batch_service.on_execution_terminated(db=db, batch_id=execution.batch_id)


_BUSY_RETRY_SECONDS = 30
_BUSY_MAX_ATTEMPTS = 20  # ~10 minutes of a chat staying busy


async def _requeue_when_busy(kwargs: dict, channel_id: str, enqueue, redis=None, base_fields=None) -> bool:
    """A continuation that could not take the chat lock is queued again
    later rather than abandoned (its work exists nowhere else). Returns
    True when requeued; past the attempt cap, reports and gives up."""
    from app.services.stream_relay import stream_relay

    attempt = int(kwargs.get("attempt", 0)) + 1
    if attempt > _BUSY_MAX_ATTEMPTS:
        await stream_relay.publish_error(
            channel_id,
            "Chat stayed busy with another running turn for too long; this continuation was dropped",
        )
        return False
    try:
        await enqueue(attempt)
    except Exception as e:
        logger.exception("Could not requeue busy continuation: %s", e)
        await stream_relay.publish_error(channel_id, f"Chat is busy and the retry could not be queued: {e}")
        return False
    await stream_relay.publish(
        channel_id,
        {"type": "status", "status": "waiting", "message": f"Chat is busy; retrying in {_BUSY_RETRY_SECONDS}s (attempt {attempt})"},
    )
    # This attempt is over; the retry runs under a new job id, so record
    # that here or this record reads "running" until it expires.
    if redis is not None and kwargs.get("job_id"):
        await redis.set(
            f"{JOB_STATUS_PREFIX}{kwargs['job_id']}",
            json.dumps({**(base_fields or {}), "status": "requeued", "retry_attempt": attempt}),
            ex=JOB_TTL,
        )
    return True


async def _stopped_by_interrupt(
    chat_id: str, kwargs: dict, channel_id: str, db,
    open_tool_calls: Optional[list[dict]] = None, rejected_ids: frozenset = frozenset(),
) -> bool:
    """A continuation queued before an operator interrupt must not run on,
    even though the running turn may have consumed the flag already.

    `open_tool_calls`: the calls of a round that has no results yet (an
    approval round); each is answered before the marker, so the assistant's
    tool_calls are never left without matching results."""
    from app.models.chat import Message
    from app.services import chat_steering
    from app.services.message_service import INTERRUPTED_TOOL_RESULT, REJECTED_TOOL_RESULT
    from app.services.stream_relay import stream_relay

    requested_at = await chat_steering.interrupt_requested_at(chat_id)
    enqueued_at = kwargs.get("enqueued_at")
    if requested_at is None or enqueued_at is None or float(enqueued_at) > requested_at:
        return False
    await chat_steering.consume_interrupt(chat_id)
    for tc in open_tool_calls or []:
        if tc.get("id"):
            db.add(Message(
                chat_id=chat_id, role="tool", tool_call_id=tc["id"], name=tc["function"]["name"],
                content=REJECTED_TOOL_RESULT if tc["id"] in rejected_ids else INTERRUPTED_TOOL_RESULT,
            ))
    if open_tool_calls:
        # Own transaction: created_at is the transaction's now(), so this
        # orders the results before the marker.
        await db.commit()
    db.add(Message(chat_id=chat_id, role="system", content=chat_steering.INTERRUPT_MARKER))
    await db.commit()
    await stream_relay.publish(channel_id, {"type": "interrupted", "content": chat_steering.INTERRUPT_MARKER})
    await stream_relay.publish_done(channel_id)
    return True


@_acting_as_job_key
@_within_job_timeout
async def execute_agent_message_job(ctx: dict, **kwargs: Any) -> None:
    """
    Process an agent message in a worker.

    Iterates send_message_stream() and publishes each chunk to Redis Stream
    via StreamRelay for the SSE endpoint to relay.
    """
    from redis.asyncio import Redis

    from app.core.database import AsyncSessionLocal
    from app.core.redis import create_redis_client
    from app.services.message_service import MessageService
    from app.services.stream_relay import stream_relay

    from app.core.telemetry import get_tracer

    job_id = kwargs["job_id"]
    chat_id = kwargs["chat_id"]
    user_id = kwargs["user_id"]
    user_token = kwargs["user_token"]
    content = kwargs["content"]
    channel_id = kwargs["channel_id"]
    # Optional — set by batch_service so the Execution row mirrors the job lifecycle.
    execution_id = kwargs.get("execution_id")
    # Agent-to-agent delegation metadata (issue #90). depth bounds chains and
    # routes to the sub-agent queue; the pending_delegation fields mean a
    # suspended parent is waiting on this job's result.
    depth = kwargs.get("depth", 0)
    pending_delegation_id = kwargs.get("pending_delegation_id")
    parent_tool_call_id = kwargs.get("parent_tool_call_id")

    from app.services.delegation import (
        current_channel_id,
        current_delegation_depth,
        current_job_meta,
    )

    current_delegation_depth.set(depth)
    current_channel_id.set(channel_id)
    current_job_meta.set({
        "execution_id": execution_id,
        "stream_ttl": kwargs.get("stream_ttl"),
        "parent_pending_delegation_id": pending_delegation_id,
        "parent_tool_call_id": parent_tool_call_id,
    })

    redis: Redis = ctx.get("redis") or create_redis_client()

    logger.info(f"Agent worker processing message for chat {chat_id} (job={job_id})")

    # Restore trace context from the enqueue side
    parent_ctx = extract_trace_context(kwargs.get("trace_context", {}))
    tracer = get_tracer()

    # Read existing status to preserve fields set at enqueue time (agent, enqueued_at)
    existing = {}
    raw = await redis.get(f"{JOB_STATUS_PREFIX}{job_id}")
    if raw:
        try:
            existing = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            pass

    # Common fields preserved across status updates
    base_fields = {
        "channel_id": channel_id,
        "queue": "agents",
        "type": "message",
        "chat_id": chat_id,
        "agent": existing.get("agent"),
        "enqueued_at": existing.get("enqueued_at"),
    }

    # Update status to running
    await _mark_running(
        ctx, redis, job_id, base_fields, chat_id=chat_id, user_token=user_token,
        execution_id=execution_id, parent_delegation_id=pending_delegation_id,
        parent_tool_call_id=parent_tool_call_id,
    )

    # Determine stream TTL (keep_alive chats get 24h TTL)
    stream_ttl = kwargs.get("stream_ttl")

    # Start ping task to keep SSE connections alive during long tool executions
    ping_task = asyncio.create_task(_ping_loop(channel_id, ttl=stream_ttl))

    completed = False
    span_ctx = {"context": parent_ctx} if parent_ctx else {}
    with attached(parent_ctx), tracer.start_as_current_span(
        "agent.job",
        **span_ctx,
        attributes={
            "chat.id": chat_id,
            "job.id": job_id,
            "job.queue": "agents",
            "agent.name": (existing.get("agent") or {}).get("name", "") if isinstance(existing.get("agent"), dict) else str(existing.get("agent", "")),
            otel_attr("span_type"): "agent",
            otel_attr("input"): content if isinstance(content, str) else json.dumps(content, default=str),
            otel_attr("thread_id"): chat_id,
            otel_attr("user_id"): user_id,
            otel_attr("labels"): json.dumps([f"agent:{existing.get('agent', '')}"]) if existing.get("agent") else "[]",
        },
    ):
        _agent_output_parts: list[str] = []
        _suspended = False
        _failed_turn: Optional[dict[str, Any]] = None
        try:
            async with AsyncSessionLocal() as db:
                message_service = MessageService(db)

                async for chunk in message_service.send_message_stream(
                    chat_id=chat_id,
                    user_id=user_id,
                    user_token=user_token,
                    content=content,
                ):
                    # Ensure chunk is a dict
                    if not isinstance(chunk, dict):
                        chunk = {"content": str(chunk)}

                    if chunk.get("content"):
                        _agent_output_parts.append(chunk["content"])
                    if _is_suspension_chunk(chunk):
                        _suspended = True
                    if chunk.get("type") == "error" and chunk.get("code"):
                        _failed_turn = chunk
                    await stream_relay.publish(channel_id, chunk, ttl=stream_ttl)

            if _failed_turn is not None:
                # The turn failed (a refused or failed LLM call, no provider,
                # ...), already streamed as its error event with its code:
                # the job fails with it, so its status, batch execution and
                # parent delegation record the code too.
                from app.providers.errors import LLMError

                raise LLMError.from_event(_failed_turn)

            # Set agent output on the current span
            _agent_output = "".join(_agent_output_parts)
            _current_span = trace.get_current_span()
            if _agent_output and _current_span:
                _current_span.set_attribute(otel_attr("output"), _agent_output)

            if _suspended:
                # The conversation continues in a delegate-resume job (issue
                # #90): no "done" event, no Execution termination, no report
                # to our own parent — the resume job owns all of that.
                await redis.set(
                    f"{JOB_STATUS_PREFIX}{job_id}",
                    json.dumps({**base_fields, "status": "suspended"}),
                    ex=JOB_TTL,
                )
                completed = True
                logger.info(f"Agent message job {job_id} suspended on delegation")
                return

            # Signal completion
            await stream_relay.publish_done(channel_id)

            # Update status
            await redis.set(
                f"{JOB_STATUS_PREFIX}{job_id}",
                json.dumps({**base_fields, "status": "completed"}),
                ex=JOB_TTL,
            )

            # Batch hook: update Execution row + check parent batch terminal status.
            if execution_id:
                final_output = {"final_message": "".join(_agent_output_parts)}
                await _terminate_execution_row(
                    execution_id, ExecutionStatus.COMPLETED, output=final_output,
                )

            # Suspend-on-delegate hook (issue #90): report our final content to
            # the suspended parent; the last child triggers the parent's resume.
            if pending_delegation_id and parent_tool_call_id:
                from app.services.delegation import on_child_complete

                await on_child_complete(
                    pending_delegation_id,
                    parent_tool_call_id,
                    json.dumps({
                        "response": "".join(_agent_output_parts),
                        "chat_id": chat_id,
                    }),
                    user_token=user_token,
                )

            completed = True
            logger.info(f"Agent message job {job_id} completed")

        except Exception as e:
            logger.error(f"Agent message job {job_id} failed: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")

            if is_redis_oom(e):
                # Redis is full: the stream and status below can't be
                # written, so leave the failure in the chat itself.
                await _persist_turn_error(chat_id, e)

            # Publish error to stream (a failed turn streamed its own already)
            if _failed_turn is None:
                await _unless_redis_full(
                    stream_relay.publish_failure(channel_id, e), f"agent job {job_id}: error event"
                )

            # Update status
            await _unless_redis_full(
                redis.set(
                    f"{JOB_STATUS_PREFIX}{job_id}",
                    json.dumps({**base_fields, "status": "failed", **_failure_fields(e)}),
                    ex=JOB_TTL,
                ),
                f"agent job {job_id}: failed status",
            )

            if execution_id:
                await _terminate_execution_row(
                    execution_id, ExecutionStatus.FAILED, error=str(e),
                )

            # A failed child must still report back, or the suspended parent
            # would wait forever on a result that is never coming.
            if pending_delegation_id and parent_tool_call_id:
                from app.services.delegation import on_child_complete

                try:
                    await on_child_complete(
                        pending_delegation_id,
                        parent_tool_call_id,
                        json.dumps({**_failure_fields(e), "chat_id": chat_id}),
                        user_token=user_token,
                    )
                except Exception:
                    logger.exception(
                        "Failed to report child failure to pending delegation %s",
                        pending_delegation_id,
                    )

            completed = True
            raise

        finally:
            ping_task.cancel()
            if not completed:
                logger.warning(f"Agent message job {job_id} cancelled/timed out")
                try:
                    await stream_relay.publish_failure(channel_id, _timed_out_error())
                    await redis.set(
                        f"{JOB_STATUS_PREFIX}{job_id}",
                        json.dumps({**base_fields, "status": "failed", **_failure_fields(_timed_out_error())}),
                        ex=JOB_TTL,
                    )
                except Exception:
                    logger.error(f"Failed to update status for cancelled agent job {job_id}")



async def _persist_turn_error(chat_id: str, error: Exception) -> None:
    """Leave a visible trace in the chat when a resumed turn dies.

    The interactive path persists an assistant error row before giving up, so
    a user can see why a turn ended. These queue re-entry points stream
    straight from the job, so a failure (a 429 on the post-tool follow-up, in
    the reported case) reached only the Redis relay and the job-status key:
    the transcript just stopped at the assistant tool-call row, which from the
    console or API is indistinguishable from a message-storage bug (#132).

    Best-effort — it must never mask the failure it is recording.
    """
    from app.core.database import AsyncSessionLocal
    from app.models.chat import Message
    from app.providers.errors import chat_error_content, turn_error

    content = chat_error_content(turn_error(error))
    try:
        async with AsyncSessionLocal() as db:
            db.add(Message(chat_id=chat_id, role="assistant", content=content))
            await db.commit()
    except Exception as persist_error:
        logger.error(
            f"Could not persist turn error for chat {chat_id}: {persist_error}"
        )

@_acting_as_job_key
@_in_enqueued_context
@traced_job("agent.resume")
@_within_job_timeout
async def execute_agent_resume_job(ctx: dict, **kwargs: Any) -> None:
    """
    Resume agent processing after tool approval in a worker.

    Handles the approval flow continuation and publishes results to Redis Stream.
    """
    from app.services.queue_service import queue_service

    from redis.asyncio import Redis

    from app.core.database import AsyncSessionLocal
    from app.core.redis import create_redis_client
    from app.models.agent import Agent
    from app.models.chat import Chat
    from app.models.pending_approval import PendingToolApproval
    from app.services.message_service import MessageService
    from app.services.stream_relay import stream_relay

    from sqlalchemy import select

    job_id = kwargs["job_id"]
    chat_id = kwargs["chat_id"]
    user_id = kwargs["user_id"]
    user_token = kwargs["user_token"]
    pending_approval_id = kwargs["pending_approval_id"]
    approved = kwargs["approved"]
    rejected_tool_call_ids = list(kwargs.get("rejected_tool_call_ids") or [])
    channel_id = kwargs["channel_id"]

    from app.services.delegation import current_channel_id, current_delegation_depth

    current_delegation_depth.set(kwargs.get("depth", 0))
    current_channel_id.set(channel_id)

    redis: Redis = ctx.get("redis") or create_redis_client()

    logger.info(f"Agent worker resuming chat {chat_id} (job={job_id}, approved={approved})")

    # Read existing status to preserve fields set at enqueue time (agent, enqueued_at)
    existing = {}
    raw = await redis.get(f"{JOB_STATUS_PREFIX}{job_id}")
    if raw:
        try:
            existing = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            pass

    # Common fields preserved across status updates
    base_fields = {
        "channel_id": channel_id,
        "queue": "agents",
        "type": "resume",
        "chat_id": chat_id,
        "agent": existing.get("agent"),
        "enqueued_at": existing.get("enqueued_at"),
    }

    await _mark_running(ctx, redis, job_id, base_fields, chat_id=chat_id, user_token=user_token)

    # Determine stream TTL (keep_alive chats get 24h TTL)
    stream_ttl = kwargs.get("stream_ttl")

    # Start ping task to keep SSE connections alive during long tool executions
    ping_task = asyncio.create_task(_ping_loop(channel_id, ttl=stream_ttl))

    completed = False
    _suspended = False
    try:
        async with AsyncSessionLocal() as db:
            # Load pending approval
            result = await db.execute(
                select(PendingToolApproval).where(
                    PendingToolApproval.id == pending_approval_id,
                )
            )
            pending_approval = result.scalar_one_or_none()

            if not pending_approval:
                await stream_relay.publish_error(channel_id, "Pending approval not found")
                return

            message_service = MessageService(db)

            # Load agent status_templates for tool status events
            status_templates = {}
            chat_result = await db.execute(
                select(Chat).where(Chat.id == chat_id)
            )
            chat_obj = chat_result.scalar_one_or_none()
            if chat_obj and chat_obj.agent_id:
                agent_result = await db.execute(
                    select(Agent).where(Agent.id == chat_obj.agent_id)
                )
                agent_obj = agent_result.scalar_one_or_none()
                if agent_obj:
                    status_templates = agent_obj.status_templates or {}

            if not approved:
                # Nothing in the round was allowed: every asked call of it is
                # a rejected one (a job queued before rounds named none).
                asked = await db.execute(
                    select(PendingToolApproval.tool_call_id).where(
                        PendingToolApproval.chat_id == pending_approval.chat_id,
                        PendingToolApproval.message_id == pending_approval.message_id,
                    )
                )
                rejected_tool_call_ids = list(dict.fromkeys([*rejected_tool_call_ids, *asked.scalars()]))

            # Approved or not, the round continues: approved calls run,
            # rejected ones get a rejection result the model reacts to.
            # Continuations re-enter the loop, so they hold the chat lock
            # like any other loop (waiting briefly for a running one).
            from app.services import chat_steering

            lock_token = await chat_steering.acquire_chat_lock_wait(chat_id)
            if lock_token is None:
                # The approval is already resolved; giving up would strand
                # it. Come back when the running turn is done.
                if await _requeue_when_busy(
                    kwargs, channel_id,
                    lambda attempt: queue_service.enqueue_agent_resume(
                        chat_id=chat_id, user_id=user_id, user_token=user_token,
                        pending_approval_id=pending_approval_id, approved=approved,
                        channel_id=channel_id, agent=base_fields.get("agent"),
                        defer_seconds=_BUSY_RETRY_SECONDS, attempt=attempt,
                        enqueued_at=kwargs.get("enqueued_at"),
                        rejected_tool_call_ids=rejected_tool_call_ids,
                        job_timeout=kwargs.get("job_timeout"),
                    ),
                    redis=redis, base_fields=base_fields,
                ):
                    completed = True
                return
            try:
                # Inside the release-protected block: a failure in the
                # stop check must not leave the chat locked.
                if await _stopped_by_interrupt(
                    chat_id, kwargs, channel_id, db,
                    open_tool_calls=pending_approval.all_tool_calls,
                    rejected_ids=frozenset(rejected_tool_call_ids),
                ):
                    completed = True
                    return
                if not approved:
                    for tc in pending_approval.all_tool_calls or []:
                        if tc.get("id") in rejected_tool_call_ids:
                            await stream_relay.publish(channel_id, {
                                "type": "tool_rejected",
                                "tool_call_id": tc.get("id"),
                                "function_namespace": pending_approval.function_namespace if tc.get("id") == pending_approval.tool_call_id else "tool",
                                "function_name": tc.get("function", {}).get("name"),
                            }, ttl=stream_ttl)
                # Run the approved calls, answer the rejected ones, and
                # stream the LLM response
                async for chunk in message_service._handle_tool_calls(
                    chat_id=chat_id,
                    user_id=user_id,
                    user_token=user_token,
                    messages=pending_approval.conversation_context["messages"],
                    tool_calls=pending_approval.all_tool_calls,
                    provider=pending_approval.conversation_context.get("provider"),
                    model=pending_approval.conversation_context.get("model"),
                    temperature=pending_approval.conversation_context.get("temperature", 0.7),
                    max_tokens=pending_approval.conversation_context.get("max_tokens"),
                    tools=pending_approval.conversation_context.get("tools", []),
                    status_templates=status_templates,
                    rejected_tool_call_ids=rejected_tool_call_ids,
                ):
                    if isinstance(chunk, dict):
                        if _is_suspension_chunk(chunk):
                            _suspended = True
                        await stream_relay.publish(channel_id, chunk, ttl=stream_ttl)
            finally:
                await chat_steering.release_chat_lock(chat_id, lock_token)

        if _suspended:
            # The approved round suspended again (delegations or an ask_user
            # question) — a resume job owns the rest; no "done" event here.
            await redis.set(
                f"{JOB_STATUS_PREFIX}{job_id}",
                json.dumps({**base_fields, "status": "suspended"}),
                ex=JOB_TTL,
            )
            completed = True
            logger.info(f"Agent resume job {job_id} suspended on pending completions")
            return

        await stream_relay.publish_done(channel_id)

        await redis.set(
            f"{JOB_STATUS_PREFIX}{job_id}",
            json.dumps({**base_fields, "status": "completed"}),
            ex=JOB_TTL,
        )

        completed = True
        logger.info(f"Agent resume job {job_id} completed")

    except Exception as e:
        logger.error(f"Agent resume job {job_id} failed: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")

        await _persist_turn_error(chat_id, e)
        await _unless_redis_full(
            stream_relay.publish_failure(channel_id, e), f"agent resume job {job_id}: error event"
        )

        await _unless_redis_full(
            redis.set(
                f"{JOB_STATUS_PREFIX}{job_id}",
                json.dumps({**base_fields, "status": "failed", **_failure_fields(e)}),
                ex=JOB_TTL,
            ),
            f"agent resume job {job_id}: failed status",
        )

        completed = True
        raise

    finally:
        ping_task.cancel()
        if not completed:
            logger.warning(f"Agent resume job {job_id} cancelled/timed out")
            try:
                await stream_relay.publish_failure(channel_id, _timed_out_error())
                await redis.set(
                    f"{JOB_STATUS_PREFIX}{job_id}",
                    json.dumps({**base_fields, "status": "failed", **_failure_fields(_timed_out_error())}),
                    ex=JOB_TTL,
                )
            except Exception:
                logger.error(f"Failed to update status for cancelled agent resume job {job_id}")


@_acting_as_job_key
@_in_enqueued_context
@traced_job("agent.delegate_resume")
@_within_job_timeout
async def execute_agent_delegate_resume_job(ctx: dict, **kwargs: Any) -> None:
    """Continue a parent conversation suspended on sub-agent delegations.

    Enqueued by the LAST finishing child (see `delegation.on_child_complete`).
    All delegate tool results are already persisted as tool-role Message rows,
    so this job only runs the follow-up phase of the tool round: rebuild the
    conversation from the DB, stream the next LLM turn, and keep going
    (further tool rounds included). Issue #90.
    """
    from app.services.queue_service import queue_service

    from redis.asyncio import Redis

    from app.core.database import AsyncSessionLocal
    from app.core.redis import create_redis_client
    from app.services.delegation import (
        current_channel_id,
        current_delegation_depth,
        current_job_meta,
    )
    from app.services.message_service import MessageService
    from app.services.stream_relay import stream_relay

    job_id = kwargs["job_id"]
    chat_id = kwargs["chat_id"]
    user_id = kwargs["user_id"]
    user_token = kwargs["user_token"]
    channel_id = kwargs["channel_id"]
    context = kwargs["conversation_context"]
    # Inherited from the suspended job (see message_service suspend block).
    execution_id = context.get("execution_id")
    parent_pending_delegation_id = context.get("parent_pending_delegation_id")
    parent_tool_call_id = context.get("parent_tool_call_id")

    current_delegation_depth.set(context.get("delegation_depth", 0))
    current_channel_id.set(channel_id)
    current_job_meta.set({
        "execution_id": execution_id,
        "stream_ttl": context.get("stream_ttl"),
        "parent_pending_delegation_id": parent_pending_delegation_id,
        "parent_tool_call_id": parent_tool_call_id,
    })

    redis: Redis = ctx.get("redis") or create_redis_client()

    logger.info(f"Agent worker resuming chat {chat_id} after delegation (job={job_id})")

    base_fields = {
        "channel_id": channel_id,
        "queue": "agents",
        "type": "delegate_resume",
        "chat_id": chat_id,
    }
    await _mark_running(
        ctx, redis, job_id, base_fields, chat_id=chat_id, user_token=user_token,
        execution_id=execution_id, parent_delegation_id=parent_pending_delegation_id,
        parent_tool_call_id=parent_tool_call_id,
    )

    stream_ttl = context.get("stream_ttl")
    ping_task = asyncio.create_task(_ping_loop(channel_id, ttl=stream_ttl))

    completed = False
    _suspended = False
    _output_parts: list[str] = []
    lock_token = None
    from app.services import chat_steering

    try:
        lock_token = await chat_steering.acquire_chat_lock_wait(chat_id)
        if lock_token is None:
            # The checkpoint is gone once this job was queued; this is the
            # only continuation. Come back when the running turn is done.
            if await _requeue_when_busy(
                kwargs, channel_id,
                lambda attempt: queue_service.enqueue_agent_delegate_resume(
                    chat_id=chat_id, user_id=user_id, user_token=user_token,
                    channel_id=channel_id, conversation_context=context,
                    defer_seconds=_BUSY_RETRY_SECONDS, attempt=attempt,
                    enqueued_at=kwargs.get("enqueued_at"),
                    job_timeout=kwargs.get("job_timeout"),
                ),
                redis=redis, base_fields=base_fields,
            ):
                completed = True
                return
            # Dropped for good: a batch or delegated child must still close
            # its Execution and tell its parent, or the parent waits forever.
            if execution_id:
                await _terminate_execution_row(
                    execution_id, ExecutionStatus.FAILED, error="Chat stayed busy; continuation dropped",
                )
            if parent_pending_delegation_id and parent_tool_call_id:
                from app.services.delegation import on_child_complete

                await on_child_complete(
                    parent_pending_delegation_id, parent_tool_call_id,
                    json.dumps({"error": "Chat stayed busy; continuation dropped", "chat_id": chat_id}),
                    user_token=user_token,
                )
            completed = True
            return
        async with AsyncSessionLocal() as db:
            if await _stopped_by_interrupt(chat_id, kwargs, channel_id, db):
                # A delegated or batch child that stops must still close its
                # Execution and tell its parent, or the parent waits forever.
                if execution_id:
                    await _terminate_execution_row(
                        execution_id, ExecutionStatus.CANCELLED, error="Interrupted by operator",
                    )
                if parent_pending_delegation_id and parent_tool_call_id:
                    from app.services.delegation import on_child_complete

                    await on_child_complete(
                        parent_pending_delegation_id,
                        parent_tool_call_id,
                        json.dumps({"error": "Interrupted by operator", "chat_id": chat_id}),
                        user_token=user_token,
                    )
                completed = True
                return
            message_service = MessageService(db)
            # The LLM usage of this continuation rides on the event that ends
            # it here (done, or round_suspended), for a caller following the
            # channel synchronously (a waited answer, an inline turn waiting
            # on a background run) to add to its own `usage`. round_suspended
            # is the round's last event: it is held back until the round is
            # over so it can carry the total.
            held_suspension = None
            with collect_usage() as usage_collector:
                async for chunk in message_service._stream_followup_after_tools(
                    chat_id=chat_id,
                    user_id=user_id,
                    user_token=user_token,
                    provider=context.get("provider"),
                    model=context.get("model"),
                    temperature=context.get("temperature", 0.7),
                    max_tokens=context.get("max_tokens"),
                    tools=context.get("tools", []),
                    status_templates=context.get("status_templates", {}),
                    depth=context.get("tool_iteration_depth", 0),
                    agent_label=context.get("agent_label"),
                    tool_summary="delegated sub-agent results",
                ):
                    if isinstance(chunk, dict):
                        if chunk.get("content"):
                            _output_parts.append(chunk["content"])
                        if _is_suspension_chunk(chunk):
                            _suspended = True
                        if chunk.get("type") == ROUND_SUSPENDED:
                            held_suspension = chunk
                            continue
                        await stream_relay.publish(channel_id, chunk, ttl=stream_ttl)
            run_usage = await _usage_totals(usage_collector)
            if held_suspension is not None:
                await stream_relay.publish(
                    channel_id, {**held_suspension, "usage": run_usage}, ttl=stream_ttl
                )

        if _suspended:
            # Suspended again on a further round of pending completions —
            # the next resume job carries the same inherited meta forward.
            await redis.set(
                f"{JOB_STATUS_PREFIX}{job_id}",
                json.dumps({**base_fields, "status": "suspended"}),
                ex=JOB_TTL,
            )
            completed = True
            logger.info(f"Delegate-resume job {job_id} suspended again on delegation")
            return

        await stream_relay.publish_done(channel_id, usage=run_usage)
        await redis.set(
            f"{JOB_STATUS_PREFIX}{job_id}",
            json.dumps({**base_fields, "status": "completed"}),
            ex=JOB_TTL,
        )

        if execution_id:
            await _terminate_execution_row(
                execution_id,
                ExecutionStatus.COMPLETED,
                output={"final_message": "".join(_output_parts)},
            )

        # This conversation is itself a delegated child: report our final
        # content to OUR suspended parent now that we're truly done.
        if parent_pending_delegation_id and parent_tool_call_id:
            from app.services.delegation import on_child_complete

            await on_child_complete(
                parent_pending_delegation_id,
                parent_tool_call_id,
                json.dumps({
                    "response": "".join(_output_parts),
                    "chat_id": chat_id,
                }),
                user_token=user_token,
            )

        completed = True
        logger.info(f"Delegate-resume job {job_id} completed")

    except Exception as e:
        logger.error(f"Delegate-resume job {job_id} failed: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        await _persist_turn_error(chat_id, e)
        await _unless_redis_full(
            stream_relay.publish_failure(channel_id, e), f"delegate-resume job {job_id}: error event"
        )
        await _unless_redis_full(
            redis.set(
                f"{JOB_STATUS_PREFIX}{job_id}",
                json.dumps({**base_fields, "status": "failed", **_failure_fields(e)}),
                ex=JOB_TTL,
            ),
            f"delegate-resume job {job_id}: failed status",
        )
        if execution_id:
            await _terminate_execution_row(
                execution_id, ExecutionStatus.FAILED, error=str(e),
            )
        if parent_pending_delegation_id and parent_tool_call_id:
            from app.services.delegation import on_child_complete

            try:
                await on_child_complete(
                    parent_pending_delegation_id,
                    parent_tool_call_id,
                    json.dumps({**_failure_fields(e), "chat_id": chat_id}),
                    user_token=user_token,
                )
            except Exception:
                logger.exception(
                    "Failed to report resume failure to pending delegation %s",
                    parent_pending_delegation_id,
                )
        completed = True
        raise

    finally:
        if lock_token is not None:
            await chat_steering.release_chat_lock(chat_id, lock_token)
        ping_task.cancel()
        if not completed:
            logger.warning(f"Delegate-resume job {job_id} cancelled/timed out")
            try:
                await stream_relay.publish_failure(channel_id, _timed_out_error())
                await redis.set(
                    f"{JOB_STATUS_PREFIX}{job_id}",
                    json.dumps({**base_fields, "status": "failed", **_failure_fields(_timed_out_error())}),
                    ex=JOB_TTL,
                )
            except Exception:
                logger.error(f"Failed to update status for cancelled delegate-resume job {job_id}")
