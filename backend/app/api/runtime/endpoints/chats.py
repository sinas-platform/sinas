"""Runtime chat endpoints - agent chat creation, message execution, and chat management."""
import asyncio
import json
import logging
import traceback
import uuid

import jsonschema
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import AsyncSessionLocal, get_db
from app.core.permissions import check_permission
from app.models import Message
from app.models.agent import Agent
from app.models.chat import Chat
from app.models.pending_approval import PendingToolApproval
from app.models.user import User
from app.providers.factory import create_provider
from app.schemas.agent import AgentResponse
from app.schemas.chat import (
    AgentChatCreateRequest,
    ChatResponse,
    ChatUpdate,
    ChatWithMessages,
    MessageResponse,
    MessageSendRequest,
    ToolApprovalRequest,
    ToolApprovalResponse,
)
from app.services.message_service import MessageService
from app.services.queue_service import queue_service
from app.services.stream_relay import stream_relay
from app.services.template_renderer import render_template
from app.utils.schema import validate_with_coercion

logger = logging.getLogger(__name__)
router = APIRouter()


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
    chat = Chat(
        user_id=user_id,
        agent_id=agent.id,
        agent_namespace=namespace,
        agent_name=agent_name,
        title=request.title or f"Chat with {namespace}/{agent_name}",
        chat_metadata={"agent_input": validated_input} if validated_input else None,
    )
    db.add(chat)
    await db.commit()
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
        await db.commit()

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
        created_at=chat.created_at,
        updated_at=chat.updated_at,
        last_message_at=None,  # New chat has no messages yet
    )


@router.post("/chats/{chat_id}/messages", response_model=MessageResponse)
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

    response_message = await message_service.send_message(
        chat_id=str(chat.id), user_id=user_id, user_token=user_token, content=content_str
    )

    return MessageResponse.model_validate(response_message)


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

    # Use message service directly (no queue — interactive chat needs low latency)
    message_service = MessageService(db)

    # Handle Union[str, list[ContentPart]] content - convert to string if needed
    content_str = (
        request.content
        if isinstance(request.content, str)
        else json.dumps([part.model_dump(exclude_none=True) for part in request.content])
    )

    # Track accumulated content for partial save
    accumulated_content = {"content": ""}

    async def event_generator():
        try:
            async for chunk in message_service.send_message_stream(
                chat_id=str(chat.id), user_id=user_id, user_token=user_token, content=content_str
            ):
                # Accumulate content BEFORE yielding
                if chunk.get("content"):
                    accumulated_content["content"] += chunk["content"]

                try:
                    if not isinstance(chunk, dict):
                        chunk = {"content": str(chunk)}
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

            yield {
                "event": "error",
                "data": json.dumps({
                    "error": "An error occurred while processing your message. Please try again.",
                    "details": str(e) if logger.level <= logging.DEBUG else None,
                })
            }

    return EventSourceResponse(event_generator())


@router.get("/chats/{chat_id}/stream/{channel_id}")
async def reconnect_stream(
    chat_id: str,
    channel_id: str,
    http_request: Request,
    last_id: str = Query(default="0", description="Last received Redis stream entry ID"),
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
        try:
            async for event in stream_relay.subscribe(channel_id, last_id=last_id):
                event_type = event.get("type", "message")

                if event_type == "done":
                    yield {"event": "done", "data": json.dumps({"status": "completed"})}
                    return
                elif event_type == "error":
                    yield {
                        "event": "error",
                        "data": json.dumps({"error": event.get("error", "An error occurred")}),
                    }
                    return
                else:
                    yield {"event": "message", "data": json.dumps(event)}
        except asyncio.CancelledError:
            return

    return EventSourceResponse(event_generator())


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

    # Update approval status
    pending_approval.approved = request.approved
    await db.commit()

    # Extract token and enqueue resume job
    user_token = http_request.headers.get("authorization", "").replace("Bearer ", "")
    channel_id = str(uuid.uuid4())

    await queue_service.enqueue_agent_resume(
        chat_id=chat_id,
        user_id=user_id,
        user_token=user_token,
        pending_approval_id=str(pending_approval.id),
        approved=request.approved,
        channel_id=channel_id,
        agent=f"{chat.agent_namespace}/{chat.agent_name}",
    )

    return JSONResponse(
        status_code=202,
        content={
            "status": "approved" if request.approved else "rejected",
            "tool_call_id": tool_call_id,
            "channel_id": channel_id,
            "message": f"Resume job enqueued. Connect to /chats/{chat_id}/stream/{channel_id} for results.",
        },
    )


@router.get("/chats", response_model=list[ChatResponse])
async def list_chats(
    request: Request,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db),
):
    """List all chats for the current user. Always filtered by user ownership."""
    user_id, permissions = current_user_data
    # No permission check needed - always filtered by user_id below

    # Subquery for last message timestamp
    last_message_subq = (
        select(Message.chat_id, func.max(Message.created_at).label("last_message_at"))
        .group_by(Message.chat_id)
        .subquery()
    )

    # Join with User and last message subquery
    result = await db.execute(
        select(Chat, User.email, last_message_subq.c.last_message_at)
        .join(User, Chat.user_id == User.id)
        .outerjoin(last_message_subq, Chat.id == last_message_subq.c.chat_id)
        .where(Chat.user_id == user_id)
        .order_by(Chat.updated_at.desc())
    )
    rows = result.all()

    # Build response with user_email and last_message_at
    chats_response = []
    for chat, email, last_message_at in rows:
        chats_response.append(
            ChatResponse(
                id=chat.id,
                user_id=chat.user_id,
                user_email=email,
                agent_id=chat.agent_id,
                agent_namespace=chat.agent_namespace,
                agent_name=chat.agent_name,
                title=chat.title,
                created_at=chat.created_at,
                updated_at=chat.updated_at,
                last_message_at=last_message_at,
            )
        )

    return chats_response


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

    return ChatWithMessages(
        id=chat.id,
        user_id=chat.user_id,
        user_email=user_email,
        agent_id=chat.agent_id,
        agent_namespace=chat.agent_namespace,
        agent_name=chat.agent_name,
        title=chat.title,
        created_at=chat.created_at,
        updated_at=chat.updated_at,
        last_message_at=last_message_at,
        messages=[MessageResponse.model_validate(msg) for msg in messages],
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

    await db.commit()
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
        created_at=chat.created_at,
        updated_at=chat.updated_at,
        last_message_at=last_message_at,
    )


@router.delete("/chats/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat(
    chat_id: str,
    http_request: Request,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db),
):
    """Delete a chat and all its messages."""
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

    await db.delete(chat)
    await db.commit()

    return None
