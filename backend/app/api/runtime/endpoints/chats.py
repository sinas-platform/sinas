"""Runtime chat endpoints - agent chat creation, message execution, and chat management."""
import asyncio
import base64
import json
import logging
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import jsonschema
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.config import settings
from app.core.database import AsyncSessionLocal, get_db
from app.core.permissions import check_permission
from app.models import Message
from app.models.agent import Agent
from app.models.chat import Chat
from app.models.pending_approval import PendingToolApproval
from app.models.user import User
from app.providers.attribution import new_chat_attribution
from app.providers.errors import ENVELOPE_KEYS, INTERNAL_ERROR, JOB_TIMEOUT, LLMError
from app.providers.factory import create_provider
from app.providers.tracking import UsageCollector, collect_usage
from app.schemas.agent import AgentResponse
from app.schemas.batch import AgentBatchRequest, AgentBatchResponse
from app.schemas.chat import (
    AgentChatCreateRequest,
    ChatResponse,
    ChatUpdate,
    ChatWithMessages,
    InvokeRequest,
    InvokeResponse,
    MessageResponse,
    MessageSendRequest,
    MessageSendResponse,
    PendingApprovalResponse,
    PendingInputAnswer,
    PendingInputAnswerAccepted,
    PendingInputAnswerResult,
    PendingInputResponse,
    ToolApprovalRequest,
    ToolApprovalResponse,
    TurnUsage,
)
from app.services import batch_service, sync_turns
from app.core.callback import CallbackURLError, validate_callback_url
from app.services.message_service import MessageService, refresh_message_tokens
from app.services.queue_service import queue_service
from app.services.stream_relay import STREAM_TTL_KEEP_ALIVE, SUBSCRIBE_WAIT_TIMEOUT, stream_relay
from app.services.stream_thinking import ThinkingFilter
from app.services.template_renderer import render_template
from app.utils.schema import validate_with_coercion

logger = logging.getLogger(__name__)
router = APIRouter()

HEARTBEAT_INTERVAL = 15  # seconds between SSE heartbeat pings


async def _collected_usage(collector: UsageCollector) -> Optional[dict]:
    """The LLM usage collected for one request, once its llm_usage rows are
    committed. None (with a logged error) only if it can't be totalled."""
    try:
        return await collector.totals()
    except Exception:
        logger.exception("Failed to total the LLM usage of this request")
        return None


def _wait_budget(chat: Chat) -> float:
    """How long a synchronous request follows its chat's turn: as long as
    one agent job of that chat may run."""
    return float(chat.job_timeout or settings.agent_job_timeout)


async def _settle(
    db: AsyncSession, chat_id: str, turn, collector: UsageCollector, *, budget: float
) -> sync_turns.TurnOutcome:
    """A non-streaming turn's outcome: a round parked on background work is
    followed for the rest of `budget` (see sync_turns), and the request's
    usage is added to that of the continuation it waited for."""
    try:
        return await sync_turns.settle_turn(
            db,
            chat_id,
            status=turn.status,
            message=turn.message,
            channel_id=turn.channel_id,
            usage_parts=[await _collected_usage(collector)],
            budget=budget,
        )
    except sync_turns.TurnFailed as e:
        raise HTTPException(502, f"The agent's turn failed: {e}")


def _turn_state(outcome: sync_turns.TurnOutcome) -> dict:
    """The TurnState fields of a response."""
    return {
        "status": outcome.status,
        "pending_inputs": [PendingInputResponse(**p) for p in outcome.pending_inputs],
        "channel_id": outcome.channel_id,
    }


async def with_heartbeat(aiter, interval: int = HEARTBEAT_INTERVAL):
    """Wrap an async iterator to yield heartbeat pings during idle periods.

    Prevents browser/proxy timeouts when no data is being sent (e.g., during
    long tool executions in inline streaming mode).

    Uses asyncio.shield to prevent cancellation of the generator's __anext__
    when sending heartbeat pings.
    """
    ait = aiter.__aiter__()
    pending = None
    while True:
        if pending is None:
            pending = asyncio.ensure_future(ait.__anext__())
        try:
            chunk = await asyncio.wait_for(asyncio.shield(pending), timeout=interval)
            pending = None
            yield chunk
        except asyncio.TimeoutError:
            # Ping without cancelling the pending __anext__
            yield {"event": "ping", "data": ""}
        except StopAsyncIteration:
            break
        except Exception as e:
            # The response has started: Starlette would only report a
            # "handled exception" RuntimeError (app/core/streaming.py).
            from app.core.streaming import log_stream_error

            log_stream_error("SSE stream", e)
            raise


# `done` payload for a stream that ends because its round is parked on the user.
SUSPENDED_DONE = {"event": "done", "data": json.dumps({"status": "suspended"})}


def round_waits_only_on_user(event: dict, asked: dict[str, Optional[str]]) -> bool:
    """Whether a round_suspended event parks the round on ask_user questions
    alone, none of which can time out while this stream would still be open.

    Answers resume on the fresh channel the answer endpoint returns, never on
    this one, so nothing more arrives here: the stream ends instead of idling
    until the relay times out. Two cases keep it open, because the round then
    resumes on this channel: it also waits on a background run or a
    sub-agent, or a question expires within the relay's idle window (an
    unanswered question's timeout resumes the round here, and the user
    should see that reply live). `asked` maps each asked tool_call_id to
    its expires_at."""
    ids = set(event.get("tool_call_ids") or [])
    if not ids or not ids <= set(asked):
        return False
    horizon = datetime.now(timezone.utc) + timedelta(seconds=SUBSCRIBE_WAIT_TIMEOUT)
    for tool_call_id in ids:
        expires_at = asked.get(tool_call_id)
        if expires_at:
            try:
                if datetime.fromisoformat(expires_at) <= horizon:
                    return False
            except ValueError:
                return False
    return True


def _error_payload(event: dict) -> dict:
    """An SSE `error` event's data: the message under `error`, and the error
    envelope (`code`, `retryable`, `upstream_status`, ...) when the failure
    has one (app/providers/errors.py)."""
    payload = {"error": event.get("error") or "An error occurred"}
    for key in ENVELOPE_KEYS:
        if event.get(key) is not None:
            payload[key] = event[key]
    return payload


# Stream events relayed under their own SSE event name; anything else is a
# `message` event.
TYPED_EVENTS = ("approval_required", "tool_start", "tool_end", "input_required", "round_suspended")


async def _relay_channel(
    channel_id: str, last_id: str, show: ThinkingFilter, asked: dict[str, Optional[str]]
):
    """Relay a stream channel as SSE until it ends: `done`, `error`, or a
    round parked on the user alone (see round_waits_only_on_user). `asked`
    holds the questions seen so far (tool_call_id → expires_at), including
    any the caller relayed before handing over to the channel. When the
    round moves to another channel (its question answered while this stream
    stayed open for its other work), the relay follows it there."""
    while channel_id:
        moved_to = None
        async for sse in _relay_one_channel(channel_id, last_id, show, asked):
            if isinstance(sse, str):
                moved_to = sse  # the inner relay ends right after it
                continue
            yield sse
        channel_id, last_id = moved_to, "0"


async def _relay_one_channel(
    channel_id: str, last_id: str, show: ThinkingFilter, asked: dict[str, Optional[str]]
):
    """_relay_channel for one channel; yields the new channel's id (a str)
    when the round moves."""
    from app.services.deferred_completions import ROUND_MOVED

    async for event in stream_relay.subscribe(channel_id, last_id=last_id):
        event_type = event.get("type", "message")
        if event_type == ROUND_MOVED:
            if event.get("channel_id"):
                yield event["channel_id"]
                return
            continue
        if event_type not in ("ping", "done", "error"):
            event = show(event)
            if event is None:
                continue
        if event_type == "ping":
            yield {"event": "ping", "data": ""}
        elif event_type == "done":
            yield {"event": "done", "data": json.dumps({"status": "completed"})}
            return
        elif event_type == "error":
            yield {"event": "error", "data": json.dumps(_error_payload(event))}
            return
        elif event_type in TYPED_EVENTS:
            yield {"event": event_type, "data": json.dumps(event)}
            if event_type == "input_required":
                asked[event.get("tool_call_id")] = event.get("expires_at")
            elif event_type == "round_suspended" and round_waits_only_on_user(event, asked):
                yield SUSPENDED_DONE
                return
        else:
            yield {"event": "message", "data": json.dumps(event)}


@router.get("/agents/default", response_model=AgentResponse)
async def get_default_agent(
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Get the default agent."""
    user_id, permissions = current_user_data

    result = await db.execute(
        select(Agent).where(Agent.is_default == True, Agent.is_active == True)
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(404, "No default agent configured")

    agent_perm = f"sinas.agents/{agent.namespace}/{agent.name}.read:own"
    if not check_permission(permissions, agent_perm):
        set_permission_used(http_request, agent_perm, has_perm=False)
        raise HTTPException(403, "Not authorized to access the default agent")

    set_permission_used(http_request, agent_perm)
    return AgentResponse.model_validate(agent)


@router.post("/agents/{namespace}/{agent_name}/chats", response_model=ChatResponse)
async def create_chat_with_agent(
    namespace: str,
    agent_name: str,
    request: AgentChatCreateRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """
    Create new chat with agent by namespace and name. Requires authentication.

    - Loads agent by namespace and name
    - Validates input against agent.input_schema
    - Creates chat with display name (e.g., "customer-support-20241222-143200-a3f7")
    - Returns chat object (id, name, agent_id, etc.)

    Note: This only creates the chat. Use POST /chats/{chat_id}/messages to send messages.
    """
    user_id, permissions = current_user_data

    # 1. Load agent by namespace and name
    agent = await Agent.get_by_name(db, namespace, agent_name)
    if not agent or not agent.is_active:
        raise HTTPException(404, f"Agent '{namespace}/{agent_name}' not found")

    # 2. Check permissions: Need agent chat permission to create chat
    agent_chat_perm = f"sinas.agents/{namespace}/{agent_name}.chat:all"

    has_permission = check_permission(permissions, agent_chat_perm)

    if not has_permission:
        set_permission_used(http_request, agent_chat_perm, has_perm=False)
        raise HTTPException(403, f"Not authorized to chat with agent '{namespace}/{agent_name}'")

    set_permission_used(http_request, agent_chat_perm)

    # 3. Validate input data against agent's input_schema (if provided)
    validated_input = request.input
    if request.input and agent.input_schema:
        try:
            validated_input = validate_with_coercion(request.input, agent.input_schema)
        except jsonschema.ValidationError as e:
            raise HTTPException(400, f"Input validation failed: {e.message}")

    # 4. Create chat with input data stored in metadata
    expires_at = None
    if request.expires_in:
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=request.expires_in)

    # Resolve keep_alive and job_timeout: chat request → agent default → global
    chat_keep_alive = request.keep_alive if request.keep_alive is not None else (agent.default_keep_alive or None)
    chat_job_timeout = request.job_timeout if request.job_timeout is not None else agent.default_job_timeout

    chat = Chat(
        user_id=user_id,
        agent_id=agent.id,
        agent_namespace=namespace,
        agent_name=agent_name,
        title=request.title or f"Chat with {namespace}/{agent_name}",
        chat_metadata={"agent_input": validated_input} if validated_input else None,
        expires_at=expires_at,
        keep_alive=chat_keep_alive,
        job_timeout=chat_job_timeout,
        attribution=await new_chat_attribution(db, user_id),
    )
    db.add(chat)
    await db.flush()
    await db.refresh(chat)

    # 5. Pre-populate with initial_messages if present (rendered with input data)
    if agent.initial_messages:
        for msg_data in agent.initial_messages:
            # Render message content with input_data if it's a string
            content = msg_data["content"]
            if isinstance(content, str) and validated_input:
                try:
                    content = render_template(content, validated_input)
                except Exception as e:
                    logger.error(f"Failed to render initial message template: {e}")

            message = Message(chat_id=chat.id, role=msg_data["role"], content=content)
            db.add(message)

    # Get user email
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one()

    return ChatResponse(
        id=chat.id,
        user_id=chat.user_id,
        user_email=user.email,
        agent_id=chat.agent_id,
        agent_namespace=chat.agent_namespace,
        agent_name=chat.agent_name,
        title=chat.title,
        archived=chat.archived,
        expires_at=chat.expires_at,
        keep_alive=chat.keep_alive,
        active_channel_id=chat.active_channel_id,
        created_at=chat.created_at,
        updated_at=chat.updated_at,
        last_message_at=None,  # New chat has no messages yet
    )


@router.post("/agents/{namespace}/{agent_name}/invoke", response_model=InvokeResponse)
async def invoke_agent(
    namespace: str,
    agent_name: str,
    request: InvokeRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """
    Synchronous agent invoke — send a message, get a reply.

    Combines chat creation + message send into one request/response.
    Supports session keys for conversation continuity across calls.

    `usage` totals the LLM calls made for this invoke — the reply and its
    tool rounds — not earlier invokes of the same session. Calls made by
    delegated sub-agents (separate jobs, under their own chats) and
    background compaction are not included.
    """
    user_id, permissions = current_user_data

    # 1. Load agent
    agent = await Agent.get_by_name(db, namespace, agent_name)
    if not agent or not agent.is_active:
        raise HTTPException(404, f"Agent '{namespace}/{agent_name}' not found")

    # 2. Check permission
    agent_chat_perm = f"sinas.agents/{namespace}/{agent_name}.chat:all"
    if not check_permission(permissions, agent_chat_perm):
        set_permission_used(http_request, agent_chat_perm, has_perm=False)
        raise HTTPException(403, f"Not authorized to chat with agent '{namespace}/{agent_name}'")
    set_permission_used(http_request, agent_chat_perm)

    # 3. Resolve or create chat
    chat = None
    is_new_chat = False

    if request.session_key:
        # Look up existing chat by session key
        result = await db.execute(
            select(Chat).where(
                Chat.agent_id == agent.id,
                Chat.session_key == request.session_key,
                Chat.archived == False,
            )
        )
        chat = result.scalar_one_or_none()

        if chat and request.reset:
            # Archive old chat, create new one
            chat.archived = True
            await db.flush()
            chat = None

        if chat:
            # Verify ownership
            if str(chat.user_id) != user_id:
                raise HTTPException(403, "Not authorized to use this session")
        else:
            is_new_chat = True
    else:
        is_new_chat = True

    if is_new_chat:
        # Validate input
        validated_input = request.input
        if request.input and agent.input_schema:
            try:
                validated_input = validate_with_coercion(request.input, agent.input_schema)
            except jsonschema.ValidationError as e:
                raise HTTPException(400, f"Input validation failed: {e.message}")

        chat = Chat(
            user_id=user_id,
            agent_id=agent.id,
            agent_namespace=namespace,
            agent_name=agent_name,
            title=f"invoke:{request.session_key or 'one-shot'}",
            session_key=request.session_key,
            chat_metadata={"agent_input": validated_input} if validated_input else None,
            job_timeout=agent.default_job_timeout,
            attribution=await new_chat_attribution(db, user_id),
        )
        db.add(chat)
        await db.flush()
        await db.refresh(chat)

        # Pre-populate initial messages
        if agent.initial_messages:
            for msg_data in agent.initial_messages:
                content = msg_data["content"]
                if isinstance(content, str) and validated_input:
                    try:
                        content = render_template(content, validated_input)
                    except Exception:
                        pass
                message = Message(chat_id=chat.id, role=msg_data["role"], content=content)
                db.add(message)

    # 4. Save user message
    content_str = (
        request.message
        if isinstance(request.message, str)
        else json.dumps([part.model_dump(exclude_none=True) for part in request.message])
    )

    # 5. Execute (inline, non-streaming)
    user_token = http_request.headers.get("authorization", "").replace("Bearer ", "")
    message_service = MessageService(db)

    # Every LLM call made in this context (the reply, its tool rounds) is
    # collected for the response's usage; see app/providers/tracking.py.
    timeout = agent.default_job_timeout or settings.function_timeout or 300
    started = asyncio.get_running_loop().time()
    # An LLM call's retry never waits past the timeout (app/services/llm_retry.py).
    from app.services.chat_steering import job_deadline

    with collect_usage() as usage_collector, job_deadline(timeout):
        try:
            turn = await asyncio.wait_for(
                message_service.run_turn(
                    chat_id=str(chat.id),
                    user_id=user_id,
                    user_token=user_token,
                    content=content_str,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            raise LLMError(JOB_TIMEOUT, f"Agent execution timed out after {timeout}s")

    # 6. Return response — following a round that waits on background work
    # for the rest of the time budget (sync_turns).
    outcome = await _settle(
        db,
        str(chat.id),
        turn,
        usage_collector,
        budget=timeout - (asyncio.get_running_loop().time() - started),
    )
    return InvokeResponse(
        reply=outcome.reply,
        chat_id=chat.id,
        session_key=request.session_key,
        usage=outcome.usage and TurnUsage(**outcome.usage),
        **_turn_state(outcome),
    )


@router.post(
    "/agents/{namespace}/{agent_name}/chats/batch",
    response_model=AgentBatchResponse,
    status_code=202,
)
async def submit_agent_batch(
    namespace: str,
    agent_name: str,
    body: AgentBatchRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Submit N agent invocations as a single batch. Each input creates a
    fresh chat and a single user-message → assistant-reply turn.

    Track aggregate progress via `GET /batches/{batch_id}`. See ADR
    2026-05-14-external-app-bulk-enqueue.md §2.4.
    """
    user_id, permissions = current_user_data

    agent = await Agent.get_by_name(db, namespace, agent_name)
    if not agent or not agent.is_active:
        raise HTTPException(404, f"Agent '{namespace}/{agent_name}' not found")

    perm = f"sinas.agents/{namespace}/{agent_name}.chat:all"
    if not check_permission(permissions, perm):
        set_permission_used(http_request, perm, has_perm=False)
        raise HTTPException(403, f"Not authorized to chat with agent '{namespace}/{agent_name}'")
    set_permission_used(http_request, perm)

    try:
        callback_url = validate_callback_url(body.callback_url)
        batch_callback_url = validate_callback_url(body.batch_callback_url)
    except CallbackURLError as e:
        raise HTTPException(status_code=400, detail=str(e))

    user_token = http_request.headers.get("authorization", "").replace("Bearer ", "")

    try:
        result = await batch_service.submit_agent_batch(
            db=db,
            user_id=user_id,
            user_token=user_token,
            namespace=namespace,
            name=agent_name,
            inputs=[item.model_dump() for item in body.inputs],
            trigger_id_prefix=body.trigger_id_prefix,
            callback_url=callback_url,
            batch_callback_url=batch_callback_url,
            execution_mode=body.execution_mode,
        )
    except batch_service.BatchError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return AgentBatchResponse(**result)


@router.post("/chats/{chat_id}/messages", response_model=MessageSendResponse)
async def send_message(
    chat_id: str,
    request: MessageSendRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """
    Send message to existing chat. Requires authentication and chat ownership.

    All agent behavior (LLM, tools, context) is defined by the agent.
    This endpoint only accepts message content.

    - Loads chat by ID
    - Verifies ownership (only chat owner can send messages)
    - Sends message using MessageService
    - Returns the reply with `usage`: the LLM calls made for this send only
      (same scope as the invoke endpoint's `usage`)
    """
    user_id, permissions = current_user_data

    # Load chat by UUID
    chat = await db.get(Chat, chat_id)
    if not chat:
        raise HTTPException(404, "Chat not found")

    # Check agent chat permission
    agent_chat_perm = f"sinas.agents/{chat.agent_namespace}/{chat.agent_name}.chat:all"
    has_permission = check_permission(permissions, agent_chat_perm)

    if not has_permission:
        set_permission_used(http_request, agent_chat_perm, has_perm=False)
        raise HTTPException(
            403, f"Not authorized to chat with agent '{chat.agent_namespace}/{chat.agent_name}'"
        )

    # Data filtering: verify ownership (users can only send to their own chats)
    if str(chat.user_id) != user_id:
        set_permission_used(http_request, agent_chat_perm, has_perm=False)
        raise HTTPException(403, "Not authorized to send messages in this chat")

    set_permission_used(http_request, agent_chat_perm)

    # Extract token for auth
    user_token = http_request.headers.get("authorization", "").replace("Bearer ", "")

    # Use message service
    message_service = MessageService(db)

    # Handle Union[str, list[ContentPart]] content - convert to string if needed
    content_str = (
        request.content
        if isinstance(request.content, str)
        else json.dumps([part.model_dump(exclude_none=True) for part in request.content])
    )

    started = asyncio.get_running_loop().time()
    with collect_usage() as usage_collector:
        turn = await message_service.run_turn(
            chat_id=str(chat.id), user_id=user_id, user_token=user_token, content=content_str
        )

    outcome = await _settle(
        db,
        str(chat.id),
        turn,
        usage_collector,
        budget=_wait_budget(chat) - (asyncio.get_running_loop().time() - started),
    )
    response = MessageSendResponse.model_validate(outcome.message or turn.message)
    response.usage = outcome.usage and TurnUsage(**outcome.usage)
    for name, value in _turn_state(outcome).items():
        setattr(response, name, value)
    return response


@router.post("/chats/{chat_id}/messages/stream")
async def stream_message(
    chat_id: str,
    request: MessageSendRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """
    Stream message to existing chat via SSE. Requires authentication and chat ownership.

    All agent behavior (LLM, tools, context) is defined by the agent.
    This endpoint only accepts message content.

    Returns EventSourceResponse with streaming chunks.
    """

    user_id, permissions = current_user_data

    # Load chat by UUID
    chat = await db.get(Chat, chat_id)
    if not chat:
        raise HTTPException(404, "Chat not found")

    # Check agent chat permission
    agent_chat_perm = f"sinas.agents/{chat.agent_namespace}/{chat.agent_name}.chat:all"
    has_permission = check_permission(permissions, agent_chat_perm)

    if not has_permission:
        set_permission_used(http_request, agent_chat_perm, has_perm=False)
        raise HTTPException(
            403, f"Not authorized to chat with agent '{chat.agent_namespace}/{chat.agent_name}'"
        )

    # Data filtering: verify ownership (users can only send to their own chats)
    if str(chat.user_id) != user_id:
        set_permission_used(http_request, agent_chat_perm, has_perm=False)
        raise HTTPException(403, "Not authorized to send messages in this chat")

    set_permission_used(http_request, agent_chat_perm)

    # Extract token for auth
    user_token = http_request.headers.get("authorization", "").replace("Bearer ", "")

    # Handle Union[str, list[ContentPart]] content - convert to string if needed
    content_str = (
        request.content
        if isinstance(request.content, str)
        else json.dumps([part.model_dump(exclude_none=True) for part in request.content])
    )

    # Resolve effective keep_alive: chat → agent → False
    effective_keep_alive = chat.keep_alive or False

    # Resolve effective job_timeout: chat → agent → global config
    effective_job_timeout = chat.job_timeout or settings.agent_job_timeout

    if effective_keep_alive:
        # --- Keep-alive mode: route through queue, subscribe to stream ---
        channel_id = str(uuid.uuid4())

        # Persist active_channel_id for reconnection
        chat.active_channel_id = channel_id
        await db.commit()

        await queue_service.enqueue_agent_message(
            chat_id=str(chat.id),
            user_id=user_id,
            user_token=user_token,
            content=content_str,
            channel_id=channel_id,
            agent=f"{chat.agent_namespace}/{chat.agent_name}",
            job_timeout=effective_job_timeout,
        )

        async def keep_alive_generator():
            show = ThinkingFilter(request.include_thinking)
            try:
                async for sse in _relay_channel(channel_id, "0", show, {}):
                    yield sse
            except asyncio.CancelledError:
                return  # Client disconnected — worker keeps running

        response = EventSourceResponse(with_heartbeat(keep_alive_generator()))
        response.headers["X-Stream-Channel"] = channel_id
        return response

    # --- Default inline mode (no queue) ---
    message_service = MessageService(db)

    # Track accumulated content for partial save
    accumulated_content = {"content": ""}

    async def event_generator():
        show = ThinkingFilter(request.include_thinking)
        asked: dict[str, Optional[str]] = {}
        suspended: Optional[dict] = None
        try:
            async for chunk in message_service.send_message_stream(
                chat_id=str(chat.id), user_id=user_id, user_token=user_token, content=content_str
            ):
                if isinstance(chunk, dict):
                    chunk = show(chunk)
                    if chunk is None:
                        continue
                # Accumulate content BEFORE yielding
                if chunk.get("content") and isinstance(chunk["content"], str):
                    accumulated_content["content"] += chunk["content"]

                try:
                    if not isinstance(chunk, dict):
                        chunk = {"content": str(chunk)}
                    # Route special event types to their own SSE event names
                    chunk_type = chunk.get("type")
                    if chunk_type == "error" and chunk.get("code"):
                        # A failed turn (a refused or failed LLM call, no
                        # provider, ...): it ends on it, as an `error` event
                        # with the error envelope, like a queued stream's. The
                        # turn already stored its error reply; nothing partial
                        # to save.
                        accumulated_content["content"] = ""
                        yield {"event": "error", "data": json.dumps(_error_payload(chunk))}
                        return
                    if chunk_type in TYPED_EVENTS:
                        yield {"event": chunk_type, "data": json.dumps(chunk)}
                        if chunk_type == "input_required":
                            asked[chunk.get("tool_call_id")] = chunk.get("expires_at")
                        elif chunk_type == "round_suspended":
                            suspended = chunk
                    else:
                        yield {"event": "message", "data": json.dumps(chunk)}
                except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
                    if accumulated_content["content"]:
                        try:
                            async with AsyncSessionLocal() as new_db:
                                partial_msg = Message(
                                    chat_id=str(chat.id),
                                    role="assistant",
                                    content=accumulated_content["content"],
                                    tool_calls=None,
                                )
                                new_db.add(partial_msg)
                                await new_db.commit()
                        except Exception as save_error:
                            logger.error(f"Failed to save partial message: {save_error}")
                    return

            # The turn above is over and stored what it said: nothing left
            # for a disconnect to save as a partial reply.
            accumulated_content["content"] = ""
            if suspended is not None:
                # The round parked (the turn above has ended and let go of
                # the chat). Parked on the user alone: done, the answer
                # resumes on the channel the answer endpoint returns.
                # Otherwise its continuation — a background run landing, a
                # question expiring — streams on the round's channel: relay
                # it here, as a keep-alive stream would.
                if round_waits_only_on_user(suspended, asked) or not suspended.get("channel_id"):
                    yield SUSPENDED_DONE
                    return
                async for sse in _relay_channel(suspended["channel_id"], "0", show, asked):
                    yield sse
                return

            yield {"event": "done", "data": json.dumps({"status": "completed"})}

        except asyncio.CancelledError:
            if accumulated_content["content"]:
                try:
                    async def save_partial():
                        async with AsyncSessionLocal() as new_db:
                            partial_msg = Message(
                                chat_id=str(chat.id),
                                role="assistant",
                                content=accumulated_content["content"],
                                tool_calls=None,
                            )
                            new_db.add(partial_msg)
                            await new_db.commit()
                    await asyncio.shield(save_partial())
                except Exception as save_error:
                    logger.error(f"Failed to save partial message: {save_error}")

        except Exception as e:
            logger.error(f"Error during message streaming: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")

            if accumulated_content["content"]:
                try:
                    async with AsyncSessionLocal() as new_db:
                        partial_msg = Message(
                            chat_id=str(chat.id),
                            role="assistant",
                            content=accumulated_content["content"],
                            tool_calls=None,
                        )
                        new_db.add(partial_msg)
                        await new_db.commit()
                except Exception as save_error:
                    logger.error(f"Failed to save partial message after error: {save_error}")

            message = "An error occurred while processing your message. Please try again."
            payload = {"error": message, "details": str(e) if logger.level <= logging.DEBUG else None}
            if isinstance(e, LLMError):
                payload.update(_error_payload(e.event_fields()))
            else:
                payload.update(LLMError(INTERNAL_ERROR, message).to_dict())
            yield {"event": "error", "data": json.dumps(payload)}

    return EventSourceResponse(with_heartbeat(event_generator()))


@router.get("/chats/{chat_id}/stream/{channel_id}")
async def reconnect_stream(
    chat_id: str,
    channel_id: str,
    http_request: Request,
    last_id: str = Query(default="0", description="Last received Redis stream entry ID"),
    include_thinking: bool = Query(
        default=False, description="Stream the model's thinking text, not only that it is thinking"
    ),
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """
    Reconnect to an existing stream channel.

    Use the channel_id from the X-Stream-Channel header and last_id from the
    last received event to resume without losing messages.
    """
    user_id, permissions = current_user_data

    # Verify chat ownership
    chat = await db.get(Chat, chat_id)
    if not chat:
        raise HTTPException(404, "Chat not found")

    if str(chat.user_id) != user_id:
        raise HTTPException(403, "Not authorized")

    # Check agent permission
    agent_chat_perm = f"sinas.agents/{chat.agent_namespace}/{chat.agent_name}.chat:all"
    if not check_permission(permissions, agent_chat_perm):
        raise HTTPException(403, "Not authorized")

    async def event_generator():
        # Reconnecting past the input_required events leaves `asked` short:
        # the stream then stays open, as before, rather than ending early.
        show = ThinkingFilter(include_thinking)
        try:
            async for sse in _relay_channel(channel_id, last_id, show, {}):
                yield sse
        except asyncio.CancelledError:
            return

    return EventSourceResponse(with_heartbeat(event_generator()))


@router.post("/chats/{chat_id}/approve-tool/{tool_call_id}")
async def approve_tool_call(
    chat_id: str,
    tool_call_id: str,
    request: ToolApprovalRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """
    Approve or reject a tool call that requires user approval.

    When a function with requires_approval=True is called by the LLM,
    execution pauses and an approval_required event is yielded.
    This endpoint enqueues the resume job and returns a channel_id
    for the client to connect to a new SSE stream.

    Returns:
        JSON with status, tool_call_id, channel_id for stream reconnection
    """
    user_id, permissions = current_user_data

    # Load chat
    chat = await db.get(Chat, chat_id)
    if not chat:
        raise HTTPException(404, "Chat not found")

    # Check agent chat permission
    agent_chat_perm = f"sinas.agents/{chat.agent_namespace}/{chat.agent_name}.chat:all"
    has_permission = check_permission(permissions, agent_chat_perm)

    if not has_permission:
        set_permission_used(http_request, agent_chat_perm, has_perm=False)
        raise HTTPException(
            403, f"Not authorized to chat with agent '{chat.agent_namespace}/{chat.agent_name}'"
        )

    # Data filtering: verify ownership
    if str(chat.user_id) != user_id:
        set_permission_used(http_request, agent_chat_perm, has_perm=False)
        raise HTTPException(403, "Not authorized to approve tools in this chat")

    set_permission_used(http_request, agent_chat_perm)

    # Load pending approval
    result = await db.execute(
        select(PendingToolApproval).where(
            PendingToolApproval.tool_call_id == tool_call_id,
            PendingToolApproval.chat_id == chat_id,
            PendingToolApproval.approved == None,  # Only pending approvals
        )
    )
    pending_approval = result.scalar_one_or_none()

    if not pending_approval:
        raise HTTPException(404, "Pending approval not found or already processed")

    # Resolve it only while it is still undecided and not past its deadline:
    # the expiry sweep resolves it the same conditional way, so exactly one of
    # the two wins (an expired approval can never be approved afterwards).
    from datetime import datetime, timezone

    from sqlalchemy import or_, update

    resolved = (
        await db.execute(
            update(PendingToolApproval)
            .where(
                PendingToolApproval.id == pending_approval.id,
                PendingToolApproval.approved.is_(None),
                or_(
                    PendingToolApproval.expires_at.is_(None),
                    PendingToolApproval.expires_at > datetime.now(timezone.utc),
                ),
            )
            .values(approved=request.approved)
            .returning(PendingToolApproval.id)
        )
    ).scalar_one_or_none()
    if resolved is None:
        await db.rollback()
        raise HTTPException(409, "This approval was already resolved or has expired")
    await db.commit()

    # "Always allow": remember the user's decision for this chat so the
    # same tool skips the ask next time (session-scoped grant).
    if request.approved and request.always_allow:
        from app.services import approval_rules

        # Session grants key on the tool name the model calls. For function
        # tools the pending row stores namespace/name; the tool name is the
        # call's function name — recover it from the stored call.
        tool_name = None
        for tc in pending_approval.all_tool_calls or []:
            if tc.get("id") == tool_call_id:
                tool_name = tc.get("function", {}).get("name")
                break
        if tool_name:
            # The approval is already resolved: a grant that fails to store
            # must not stop the approved work from resuming (the user is at
            # worst asked again next time).
            try:
                await approval_rules.add_session_grant(chat_id, tool_name)
            except Exception as e:
                logger.warning("Session grant for %s in chat %s not stored: %s", tool_name, chat_id, e)

    # A round with several asked calls resumes once, after the last
    # decision: every approval row stores the whole round, so resuming now
    # would run the still-undecided calls unchecked.
    from app.services import approval_rules

    decision = await approval_rules.round_decision(db, pending_approval)
    channel_id = approval_rules.round_channel_id(pending_approval)
    if decision is None:
        return JSONResponse(
            status_code=202,
            content={
                "status": "approved" if request.approved else "rejected",
                "tool_call_id": tool_call_id,
                "channel_id": channel_id,
                "resume": "waiting",
                "message": "Decision recorded; the round resumes once every requested approval is answered.",
            },
        )

    # Extract token and enqueue resume job
    user_token = http_request.headers.get("authorization", "").replace("Bearer ", "")

    await queue_service.enqueue_agent_resume(
        chat_id=chat_id,
        user_id=user_id,
        user_token=user_token,
        pending_approval_id=str(pending_approval.id),
        approved=bool(decision["approved_ids"]),
        channel_id=channel_id,
        agent=f"{chat.agent_namespace}/{chat.agent_name}",
        rejected_tool_call_ids=decision["rejected_ids"],
        job_id=approval_rules.round_job_id(pending_approval),
    )

    # Queued: an expired sibling counted as rejected above is recorded as
    # such now, so the expiry sweep has nothing left to resume. Had the
    # enqueue failed, it stays undecided and the sweep resumes the round
    # (the fixed job id makes any overlap a no-op).
    await db.execute(
        update(PendingToolApproval)
        .where(
            PendingToolApproval.message_id == pending_approval.message_id,
            PendingToolApproval.approved.is_(None),
        )
        .values(approved=False)
    )
    await db.commit()

    return JSONResponse(
        status_code=202,
        content={
            "status": "approved" if request.approved else "rejected",
            "tool_call_id": tool_call_id,
            "channel_id": channel_id,
            "resume": "queued",
            "message": f"Resume job enqueued. Connect to /chats/{chat_id}/stream/{channel_id} for results.",
        },
    )


async def _authorize_chat_access(
    db: AsyncSession, http_request: Request, chat_id: str, current_user_data: tuple
) -> Chat:
    """Shared ownership + agent-chat permission gate for chat sub-resources."""
    user_id, permissions = current_user_data

    chat = await db.get(Chat, chat_id)
    if not chat:
        raise HTTPException(404, "Chat not found")

    agent_chat_perm = f"sinas.agents/{chat.agent_namespace}/{chat.agent_name}.chat:all"
    if not check_permission(permissions, agent_chat_perm):
        set_permission_used(http_request, agent_chat_perm, has_perm=False)
        raise HTTPException(
            403, f"Not authorized to chat with agent '{chat.agent_namespace}/{chat.agent_name}'"
        )
    if str(chat.user_id) != user_id:
        set_permission_used(http_request, agent_chat_perm, has_perm=False)
        raise HTTPException(403, "Not authorized to access this chat")

    set_permission_used(http_request, agent_chat_perm)
    return chat


@router.get("/chats/{chat_id}/pending-input", response_model=list[PendingInputResponse])
async def list_pending_input(
    chat_id: str,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """List the ask_user questions this chat is currently suspended on."""
    from app.services import deferred_completions

    await _authorize_chat_access(db, http_request, chat_id, current_user_data)
    return [
        PendingInputResponse(**{k: v for k, v in item.items() if k != "pending_completion_id"})
        for item in await deferred_completions.list_pending_inputs(db, chat_id)
    ]


@router.post(
    "/chats/{chat_id}/pending-input/{tool_call_id}",
    status_code=202,
    response_model=PendingInputAnswerAccepted,
    responses={
        200: {
            "model": PendingInputAnswerResult,
            "description": "With `wait: true`: the resumed turn's result.",
        },
    },
)
async def answer_pending_input(
    chat_id: str,
    tool_call_id: str,
    request: PendingInputAnswer,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Answer an ask_user question the conversation is suspended on.

    The answer becomes the tool result of the suspended ask_user call. When
    it is the round's last outstanding completion, a resume job continues
    the conversation on a fresh stream channel (returned as `channel_id` —
    same reconnect contract as tool approval); otherwise the round keeps
    waiting for its other completions and `resumed` is false. This works
    the same whichever way the turn was started (queued, inline stream,
    non-streaming send, invoke).

    With `wait: true` the request instead waits for the resumed turn and
    returns its result (200, PendingInputAnswerResult): the final reply,
    the next questions if the agent asks again, or `in_progress` with the
    channel when the turn outlasts the chat's job timeout.
    """
    chat = await _authorize_chat_access(db, http_request, chat_id, current_user_data)
    user_token = http_request.headers.get("authorization", "").replace("Bearer ", "")
    started = asyncio.get_running_loop().time()

    try:
        answered = await sync_turns.answer_question(
            db, chat_id, tool_call_id, request.answer, user_token=user_token
        )
    except sync_turns.AnswerNotFound:
        raise HTTPException(404, "Pending input not found or already answered")
    except sync_turns.AnswerConflict:
        # Raced another completion of the same entry (double submit, expiry).
        raise HTTPException(409, "Pending input was already resolved")

    if request.wait:
        try:
            outcome = await sync_turns.settle_answer(
                db,
                chat_id,
                answered,
                budget=_wait_budget(chat) - (asyncio.get_running_loop().time() - started),
            )
        except sync_turns.TurnFailed as e:
            raise HTTPException(502, f"The agent's turn failed: {e}")
        result = PendingInputAnswerResult(
            tool_call_id=tool_call_id,
            chat_id=chat.id,
            reply=outcome.reply,
            message=MessageResponse.model_validate(outcome.message) if outcome.message else None,
            usage=outcome.usage and TurnUsage(**outcome.usage),
            **_turn_state(outcome),
        )
        return JSONResponse(status_code=200, content=result.model_dump(mode="json"))

    resumed, pending = answered.resumed, answered.resume_pending
    # The channel the conversation actually resumes on (a delegated child
    # keeps its original one, which its parent is waiting on).
    channel_id = answered.channel_id
    if resumed:
        message = f"Resume job enqueued. Connect to /chats/{chat_id}/stream/{channel_id} for results."
    elif pending:
        message = (
            "Answer recorded, but the resume could not be queued yet; it is retried "
            f"within a minute or two on /chats/{chat_id}/stream/{channel_id}."
        )
    else:
        message = "Answer recorded; the conversation resumes when its remaining completions land."
    return JSONResponse(
        status_code=202,
        content={
            "status": "answered",
            "tool_call_id": tool_call_id,
            "resumed": resumed,
            "resume_pending": pending,
            "channel_id": channel_id if (resumed or pending) else None,
            "message": message,
        },
    )


CHATS_PAGE_DEFAULT = 50
CHATS_PAGE_MAX = 200
_CURSOR_MAX_LEN = 256
# What GET /chats returns of each chat (with the creator's email and the last
# message's time). Not chat_metadata: agent input, which can be large.
_CHAT_LIST_COLUMNS = (
    Chat.id,
    Chat.user_id,
    Chat.agent_id,
    Chat.agent_namespace,
    Chat.agent_name,
    Chat.title,
    Chat.archived,
    Chat.expires_at,
    Chat.keep_alive,
    Chat.active_channel_id,
    Chat.created_at,
    Chat.updated_at,
)


def encode_chat_cursor(updated_at: datetime, chat_id: uuid.UUID) -> str:
    """Opaque keyset cursor: base64url of the last row's (updated_at, id)."""
    raw = json.dumps({"u": updated_at.isoformat(), "i": str(chat_id)}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _parse_chat_cursor(cursor: str) -> Optional[tuple[datetime, uuid.UUID]]:
    """(updated_at, id) from a cursor, or None unless it has exactly the
    shape encode_chat_cursor writes: {"u": aware ISO datetime, "i": UUID}."""
    if len(cursor) > _CURSOR_MAX_LEN:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii") + b"=" * (-len(cursor) % 4))
        data = json.loads(raw.decode("utf-8"))
    except ValueError:  # non-ASCII, bad base64, not UTF-8, not JSON
        return None
    if not isinstance(data, dict) or set(data) != {"u", "i"}:
        return None
    u, i = data["u"], data["i"]
    if not isinstance(u, str) or not isinstance(i, str):
        return None
    try:
        updated_at = datetime.fromisoformat(u)
        chat_id = uuid.UUID(i)
        if updated_at.tzinfo is None:  # ambiguous against timestamptz
            return None
        # In UTC, as the driver sends it; out of range (year 1 at +14:00) fails here.
        updated_at = updated_at.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None
    return updated_at, chat_id


def decode_chat_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """Inverse of encode_chat_cursor; anything malformed is a 400."""
    parsed = _parse_chat_cursor(cursor)
    if parsed is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid cursor")
    return parsed


@router.get("/chats", response_model=list[ChatResponse])
async def list_chats(
    request: Request,
    response: Response,
    include_archived: bool = Query(False, description="Include archived chats"),
    q: Optional[str] = Query(
        None, max_length=200, description="Only chats whose title contains this text (case-insensitive)"
    ),
    limit: Optional[int] = Query(
        None,
        ge=1,
        le=CHATS_PAGE_MAX,
        description=(
            f"Page size (1-{CHATS_PAGE_MAX}). Passing `limit` or `cursor` turns on pagination; "
            "without either, every matching chat is returned, as before, up to "
            "CHATS_UNPAGINATED_MAX (default 10000): past that the response is truncated, "
            "says so in `X-Truncated: true`, and carries the cursor for the rest."
        ),
    ),
    cursor: Optional[str] = Query(
        None,
        description=(
            "Opaque cursor from a previous page's `X-Next-Cursor` header. "
            f"Pages hold {CHATS_PAGE_DEFAULT} chats unless `limit` says otherwise."
        ),
    ),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db),
):
    """List the caller's chats, most recently updated first.

    Always filtered by user ownership. The body is a list of chats either
    way; when paginated (`limit` or `cursor` given) and more chats follow,
    the `X-Next-Cursor` response header carries the cursor for the next
    page, and `Link` the same as a `rel="next"` URL. No header: last page.
    Pages are keyset-based on (updated_at, id): chats created or updated
    while a client pages move to the front, so later pages neither repeat
    nor skip the other chats; the moved ones show on a fresh first page.

    Unpaginated, the response holds at most CHATS_UNPAGINATED_MAX chats (one
    response of a user's every chat could exhaust the backend's memory).
    Past that it is the first CHATS_UNPAGINATED_MAX, with `X-Truncated: true`
    and the same `X-Next-Cursor`/`Link` headers a page carries; `Link` asks
    for pages of CHATS_PAGE_MAX.
    """
    user_id, permissions = current_user_data
    # No permission check needed - always filtered by user_id below

    paginated = limit is not None or cursor is not None
    # Unpaginated callers get every chat up to the cap: one more is fetched to
    # tell whether there were more.
    page_size = (limit or CHATS_PAGE_DEFAULT) if paginated else settings.chats_unpaginated_max

    filters = [Chat.user_id == user_id]
    if not include_archived:
        filters.append(Chat.archived.is_(False))
    if q and q.strip():
        filters.append(Chat.title.icontains(q.strip(), autoescape=True))
    if cursor is not None:
        after_updated_at, after_id = decode_chat_cursor(cursor)
        filters.append(tuple_(Chat.updated_at, Chat.id) < (after_updated_at, after_id))

    # Select the page of chats first (ix_chats_user_id_updated_at_id), then
    # look up each one's last message on ix_messages_chat_id_created_at: one
    # index probe per returned chat instead of aggregating every message.
    # Only the columns the response carries (_CHAT_LIST_COLUMNS), as plain
    # rows: no ORM entities to track.
    page = (
        select(*_CHAT_LIST_COLUMNS)
        .where(*filters)
        .order_by(Chat.updated_at.desc(), Chat.id.desc())
        .limit(page_size + 1)
        .subquery("page")
    )
    last_message_at = (
        select(func.max(Message.created_at)).where(Message.chat_id == page.c.id).scalar_subquery()
    )
    result = await db.execute(
        select(page, User.email.label("user_email"), last_message_at.label("last_message_at"))
        .join(User, page.c.user_id == User.id)
        .order_by(page.c.updated_at.desc(), page.c.id.desc())
    )
    rows = result.mappings().all()

    if len(rows) > page_size:
        rows = rows[:page_size]
        last = rows[-1]
        next_cursor = encode_chat_cursor(last["updated_at"], last["id"])
        next_url = request.url.include_query_params(
            cursor=next_cursor, limit=page_size if paginated else CHATS_PAGE_MAX
        )
        response.headers["X-Next-Cursor"] = next_cursor
        response.headers["Link"] = f'<{next_url.path}?{next_url.query}>; rel="next"'
        if not paginated:
            response.headers["X-Truncated"] = "true"
            logger.warning(
                "GET /chats without limit or cursor for user %s: more than %d chats, "
                "returned the first %d with X-Truncated (the client should page)",
                user_id,
                page_size,
                page_size,
            )

    return [ChatResponse(**row) for row in rows]


@router.get("/chats/{chat_id}", response_model=ChatWithMessages)
async def get_chat(
    request: Request,
    chat_id: str,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db),
):
    """Get a chat with all messages."""
    user_id, permissions = current_user_data

    # Get chat with user email (filtered by user_id for data privacy)
    result = await db.execute(
        select(Chat, User.email)
        .join(User, Chat.user_id == User.id)
        .where(Chat.id == chat_id, Chat.user_id == user_id)
    )
    row = result.one_or_none()

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")

    chat, user_email = row

    # Check agent chat permission
    agent_chat_perm = f"sinas.agents/{chat.agent_namespace}/{chat.agent_name}.chat:all"
    has_permission = check_permission(permissions, agent_chat_perm)

    if not has_permission:
        set_permission_used(request, agent_chat_perm, has_perm=False)
        raise HTTPException(
            403, f"Not authorized to chat with agent '{chat.agent_namespace}/{chat.agent_name}'"
        )

    set_permission_used(request, agent_chat_perm)

    # Get messages
    result = await db.execute(
        select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at)
    )
    messages = result.scalars().all()

    # Calculate last message timestamp
    last_message_at = messages[-1].created_at if messages else None

    # Refresh expired tokens (image URLs + component render tokens) in message content
    message_responses = []
    for msg in messages:
        resp = MessageResponse.model_validate(msg)
        resp.content = refresh_message_tokens(resp.content, user_id)
        message_responses.append(resp)

    # Load any unresolved pending approvals for this chat
    approval_result = await db.execute(
        select(PendingToolApproval).where(
            PendingToolApproval.chat_id == chat_id,
            PendingToolApproval.approved == None,
        )
    )
    pending = [
        PendingApprovalResponse(
            tool_call_id=pa.tool_call_id,
            function_namespace=pa.function_namespace,
            function_name=pa.function_name,
            arguments=pa.arguments or {},
        )
        for pa in approval_result.scalars().all()
    ]

    # Load any open ask_user questions (deferred tool round suspensions)
    from app.services import deferred_completions

    pending_inputs = [
        PendingInputResponse(
            **{k: v for k, v in item.items() if k != "pending_completion_id"}
        )
        for item in await deferred_completions.list_pending_inputs(db, chat_id)
    ]

    from app.services.workbench import chat_has_workbench_enabled

    return ChatWithMessages(
        id=chat.id,
        user_id=chat.user_id,
        user_email=user_email,
        agent_id=chat.agent_id,
        agent_namespace=chat.agent_namespace,
        agent_name=chat.agent_name,
        title=chat.title,
        archived=chat.archived,
        expires_at=chat.expires_at,
        keep_alive=chat.keep_alive,
        active_channel_id=chat.active_channel_id,
        workbench_enabled=await chat_has_workbench_enabled(db, chat),
        created_at=chat.created_at,
        updated_at=chat.updated_at,
        last_message_at=last_message_at,
        messages=message_responses,
        pending_approvals=pending,
        pending_inputs=pending_inputs,
    )


@router.put("/chats/{chat_id}", response_model=ChatResponse)
async def update_chat(
    chat_id: str,
    request: ChatUpdate,
    http_request: Request,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db),
):
    """Update a chat."""
    user_id, permissions = current_user_data

    # Get chat (filtered by user_id for data privacy)
    result = await db.execute(select(Chat).where(Chat.id == chat_id, Chat.user_id == user_id))
    chat = result.scalar_one_or_none()

    if not chat:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")

    # Check agent chat permission
    agent_chat_perm = f"sinas.agents/{chat.agent_namespace}/{chat.agent_name}.chat:all"
    has_permission = check_permission(permissions, agent_chat_perm)

    if not has_permission:
        set_permission_used(http_request, agent_chat_perm, has_perm=False)
        raise HTTPException(
            403, f"Not authorized to chat with agent '{chat.agent_namespace}/{chat.agent_name}'"
        )

    set_permission_used(http_request, agent_chat_perm)

    if request.title is not None:
        chat.title = request.title
    if request.archived is not None:
        chat.archived = request.archived

    await db.flush()
    await db.refresh(chat)

    # Get user email and last message timestamp
    user_result = await db.execute(select(User).where(User.id == chat.user_id))
    user = user_result.scalar_one()

    # Get last message timestamp
    last_msg_result = await db.execute(
        select(func.max(Message.created_at)).where(Message.chat_id == chat_id)
    )
    last_message_at = last_msg_result.scalar()

    return ChatResponse(
        id=chat.id,
        user_id=chat.user_id,
        user_email=user.email,
        agent_id=chat.agent_id,
        agent_namespace=chat.agent_namespace,
        agent_name=chat.agent_name,
        title=chat.title,
        archived=chat.archived,
        expires_at=chat.expires_at,
        keep_alive=chat.keep_alive,
        active_channel_id=chat.active_channel_id,
        created_at=chat.created_at,
        updated_at=chat.updated_at,
        last_message_at=last_message_at,
    )


@router.post("/chats/{chat_id}/interrupt")
async def interrupt_chat(
    chat_id: str,
    http_request: Request,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db),
):
    """Stop the chat's running agent loop without touching the transcript
    (issue #142).

    Arms a cooperative interrupt: the loop stops at its next tool-round
    boundary and appends an 'interrupted by operator' system marker; queued
    continuation jobs that start within the flag's TTL stop immediately.
    The chat and its messages stay intact, and the next user message clears
    the flag and starts a fresh turn.
    """
    user_id, permissions = current_user_data

    result = await db.execute(select(Chat).where(Chat.id == chat_id, Chat.user_id == user_id))
    chat = result.scalar_one_or_none()
    if not chat:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")

    agent_chat_perm = f"sinas.agents/{chat.agent_namespace}/{chat.agent_name}.chat:all"
    if not check_permission(permissions, agent_chat_perm):
        set_permission_used(http_request, agent_chat_perm, has_perm=False)
        raise HTTPException(
            403, f"Not authorized to chat with agent '{chat.agent_namespace}/{chat.agent_name}'"
        )
    set_permission_used(http_request, agent_chat_perm)

    from app.services import chat_steering

    was_running = await chat_steering.is_chat_locked(chat_id)
    await chat_steering.request_interrupt(chat_id, requested_by=user_id)
    return {"interrupted": True, "was_running": was_running}


@router.delete("/chats/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat(
    chat_id: str,
    http_request: Request,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db),
):
    """Archive a chat (soft-delete). The chat is hidden from listings but can be restored."""
    user_id, permissions = current_user_data

    # Get chat (filtered by user_id for data privacy)
    result = await db.execute(select(Chat).where(Chat.id == chat_id, Chat.user_id == user_id))
    chat = result.scalar_one_or_none()

    if not chat:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")

    # Check agent chat permission
    agent_chat_perm = f"sinas.agents/{chat.agent_namespace}/{chat.agent_name}.chat:all"
    has_permission = check_permission(permissions, agent_chat_perm)

    if not has_permission:
        set_permission_used(http_request, agent_chat_perm, has_perm=False)
        raise HTTPException(
            403, f"Not authorized to chat with agent '{chat.agent_namespace}/{chat.agent_name}'"
        )

    set_permission_used(http_request, agent_chat_perm)

    chat.archived = True
    await db.flush()

    return None
