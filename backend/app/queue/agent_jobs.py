"""arq job handlers for agent message processing."""
import json
import logging
import traceback
from typing import Any

from app.core.config import settings
from app.services.queue_service import JOB_STATUS_PREFIX, JOB_TTL

logger = logging.getLogger(__name__)


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

    job_id = kwargs["job_id"]
    chat_id = kwargs["chat_id"]
    user_id = kwargs["user_id"]
    user_token = kwargs["user_token"]
    content = kwargs["content"]
    channel_id = kwargs["channel_id"]

    redis: Redis = ctx.get("redis") or Redis.from_url(settings.redis_url, decode_responses=True)

    logger.info(f"Agent worker processing message for chat {chat_id} (job={job_id})")

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

                await stream_relay.publish(channel_id, chunk)

        # Signal completion
        await stream_relay.publish_done(channel_id)

        # Update status
        await redis.set(
            f"{JOB_STATUS_PREFIX}{job_id}",
            json.dumps({**base_fields, "status": "completed"}),
            ex=JOB_TTL,
        )

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

        raise


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

            if approved:
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
                ):
                    if isinstance(chunk, dict):
                        await stream_relay.publish(channel_id, chunk)
            else:
                # Handle rejection - publish rejection info
                await stream_relay.publish(channel_id, {
                    "type": "tool_rejected",
                    "tool_call_id": pending_approval.tool_call_id,
                    "function_namespace": pending_approval.function_namespace,
                    "function_name": pending_approval.function_name,
                })

        await stream_relay.publish_done(channel_id)

        await redis.set(
            f"{JOB_STATUS_PREFIX}{job_id}",
            json.dumps({**base_fields, "status": "completed"}),
            ex=JOB_TTL,
        )

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

        raise
