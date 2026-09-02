"""arq job handlers for agent message processing."""
import asyncio
import json
import logging
import traceback
import uuid as uuid_lib
from datetime import datetime, timezone
from typing import Any, Optional

from opentelemetry import trace
from sqlalchemy import select

from app.core.config import settings
from app.core.telemetry import otel_attr
from app.models.execution import Execution, ExecutionStatus
from app.services.queue_service import JOB_STATUS_PREFIX, JOB_TTL

logger = logging.getLogger(__name__)

PING_INTERVAL = 15  # seconds between keep-alive pings


def _is_suspension_chunk(chunk: dict) -> bool:
    """A chunk announcing the tool round suspended on pending completions
    (delegations, ask_user questions). The job must then end WITHOUT a
    "done" event or terminal bookkeeping — a resume job owns the rest of
    the conversation."""
    from app.services.deferred_completions import SUSPENSION_EVENT_TYPES

    return chunk.get("type") in SUSPENSION_EVENT_TYPES


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


async def execute_agent_message_job(ctx: dict, **kwargs: Any) -> None:
    """
    Process an agent message in a worker.

    Iterates send_message_stream() and publishes each chunk to Redis Stream
    via StreamRelay for the SSE endpoint to relay.
    """
    from redis.asyncio import Redis

    from app.core.database import AsyncSessionLocal
    from app.services.message_service import MessageService
    from app.services.stream_relay import stream_relay

    from app.core.telemetry import extract_trace_context, get_tracer

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

    redis: Redis = ctx.get("redis") or Redis.from_url(settings.redis_url, decode_responses=True)

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
    await redis.set(
        f"{JOB_STATUS_PREFIX}{job_id}",
        json.dumps({**base_fields, "status": "running"}),
        ex=JOB_TTL,
    )

    # Determine stream TTL (keep_alive chats get 24h TTL)
    stream_ttl = kwargs.get("stream_ttl")

    # Start ping task to keep SSE connections alive during long tool executions
    ping_task = asyncio.create_task(_ping_loop(channel_id, ttl=stream_ttl))

    completed = False
    span_ctx = {"context": parent_ctx} if parent_ctx else {}
    with tracer.start_as_current_span(
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
                    await stream_relay.publish(channel_id, chunk, ttl=stream_ttl)

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

            # Publish error to stream
            await stream_relay.publish_error(channel_id, str(e))

            # Update status
            await redis.set(
                f"{JOB_STATUS_PREFIX}{job_id}",
                json.dumps({**base_fields, "status": "failed", "error": str(e)}),
                ex=JOB_TTL,
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
                        json.dumps({"error": str(e), "chat_id": chat_id}),
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
                    await stream_relay.publish_error(channel_id, "Job cancelled or timed out")
                    await redis.set(
                        f"{JOB_STATUS_PREFIX}{job_id}",
                        json.dumps({**base_fields, "status": "failed", "error": "Job cancelled or timed out"}),
                        ex=JOB_TTL,
                    )
                except Exception:
                    logger.error(f"Failed to update status for cancelled agent job {job_id}")


async def execute_agent_resume_job(ctx: dict, **kwargs: Any) -> None:
    """
    Resume agent processing after tool approval in a worker.

    Handles the approval flow continuation and publishes results to Redis Stream.
    """
    from redis.asyncio import Redis

    from app.core.database import AsyncSessionLocal
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
    channel_id = kwargs["channel_id"]

    from app.services.delegation import current_channel_id, current_delegation_depth

    current_delegation_depth.set(kwargs.get("depth", 0))
    current_channel_id.set(channel_id)

    redis: Redis = ctx.get("redis") or Redis.from_url(settings.redis_url, decode_responses=True)

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

    await redis.set(
        f"{JOB_STATUS_PREFIX}{job_id}",
        json.dumps({**base_fields, "status": "running"}),
        ex=JOB_TTL,
    )

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

            if approved:
                # Continuations re-enter the loop, so they hold the chat lock
                # like any other loop (waiting briefly for a running one).
                from app.services import chat_steering

                lock_token = await chat_steering.acquire_chat_lock_wait(chat_id)
                if lock_token is None:
                    await stream_relay.publish_error(
                        channel_id, "Chat is busy with another running turn — try again"
                    )
                    return
                try:
                    # Execute the approved tool calls and stream the LLM response
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
                    ):
                        if isinstance(chunk, dict):
                            if _is_suspension_chunk(chunk):
                                _suspended = True
                            await stream_relay.publish(channel_id, chunk, ttl=stream_ttl)
                finally:
                    await chat_steering.release_chat_lock(chat_id, lock_token)
            else:
                # Handle rejection - publish rejection info
                await stream_relay.publish(channel_id, {
                    "type": "tool_rejected",
                    "tool_call_id": pending_approval.tool_call_id,
                    "function_namespace": pending_approval.function_namespace,
                    "function_name": pending_approval.function_name,
                }, ttl=stream_ttl)

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

        await stream_relay.publish_error(channel_id, str(e))

        await redis.set(
            f"{JOB_STATUS_PREFIX}{job_id}",
            json.dumps({**base_fields, "status": "failed", "error": str(e)}),
            ex=JOB_TTL,
        )

        completed = True
        raise

    finally:
        ping_task.cancel()
        if not completed:
            logger.warning(f"Agent resume job {job_id} cancelled/timed out")
            try:
                await stream_relay.publish_error(channel_id, "Job cancelled or timed out")
                await redis.set(
                    f"{JOB_STATUS_PREFIX}{job_id}",
                    json.dumps({**base_fields, "status": "failed", "error": "Job cancelled or timed out"}),
                    ex=JOB_TTL,
                )
            except Exception:
                logger.error(f"Failed to update status for cancelled agent resume job {job_id}")


async def execute_agent_delegate_resume_job(ctx: dict, **kwargs: Any) -> None:
    """Continue a parent conversation suspended on sub-agent delegations.

    Enqueued by the LAST finishing child (see `delegation.on_child_complete`).
    All delegate tool results are already persisted as tool-role Message rows,
    so this job only runs the follow-up phase of the tool round: rebuild the
    conversation from the DB, stream the next LLM turn, and keep going
    (further tool rounds included). Issue #90.
    """
    from redis.asyncio import Redis

    from app.core.database import AsyncSessionLocal
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

    redis: Redis = ctx.get("redis") or Redis.from_url(settings.redis_url, decode_responses=True)

    logger.info(f"Agent worker resuming chat {chat_id} after delegation (job={job_id})")

    base_fields = {
        "channel_id": channel_id,
        "queue": "agents",
        "type": "delegate_resume",
        "chat_id": chat_id,
    }
    await redis.set(
        f"{JOB_STATUS_PREFIX}{job_id}",
        json.dumps({**base_fields, "status": "running"}),
        ex=JOB_TTL,
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
            await stream_relay.publish_error(
                channel_id, "Chat is busy with another running turn — try again"
            )
            return
        async with AsyncSessionLocal() as db:
            message_service = MessageService(db)
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
                    await stream_relay.publish(channel_id, chunk, ttl=stream_ttl)

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

        await stream_relay.publish_done(channel_id)
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
        await stream_relay.publish_error(channel_id, str(e))
        await redis.set(
            f"{JOB_STATUS_PREFIX}{job_id}",
            json.dumps({**base_fields, "status": "failed", "error": str(e)}),
            ex=JOB_TTL,
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
                    json.dumps({"error": str(e), "chat_id": chat_id}),
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
                await stream_relay.publish_error(channel_id, "Job cancelled or timed out")
                await redis.set(
                    f"{JOB_STATUS_PREFIX}{job_id}",
                    json.dumps({**base_fields, "status": "failed", "error": "Job cancelled or timed out"}),
                    ex=JOB_TTL,
                )
            except Exception:
                logger.error(f"Failed to update status for cancelled delegate-resume job {job_id}")
