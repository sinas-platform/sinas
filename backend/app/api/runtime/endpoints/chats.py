"""Runtime chat endpoints - agent chat creation, message execution, and chat management."""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List
from sse_starlette.sse import EventSourceResponse
import jsonschema
from datetime import datetime
import uuid
import json

from app.core.database import get_db
from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.models.agent import Agent
from app.models.chat import Chat
from app.models import Message
from app.services.message_service import MessageService
from app.schemas.chat import AgentChatCreateRequest, MessageSendRequest, ChatResponse, MessageResponse, ChatUpdate, ChatWithMessages

router = APIRouter()


@router.post("/agents/{namespace}/{agent_name}/chats", response_model=ChatResponse)
async def create_chat_with_agent(
    namespace: str,
    agent_name: str,
    request: AgentChatCreateRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions)
):
    """
    Create new chat with agent by namespace and name. Requires authentication.

    - Loads agent by namespace and name
    - Validates input against agent.input_schema
    - Creates chat with display name (e.g., "customer-support-20241222-143200-a3f7")
    - Returns chat object (id, name, agent_id, etc.)

    Note: This only creates the chat. Use POST /chats/{chat_id}/messages to send messages.
    """
    from app.core.permissions import check_permission

    user_id, permissions = current_user_data

    # 1. Load agent by namespace and name
    agent = await Agent.get_by_name(db, namespace, agent_name)
    if not agent or not agent.is_active:
        raise HTTPException(404, f"Agent '{namespace}/{agent_name}' not found")

    # 2. Check permissions: Need agent read permission
    agent_perm = f"sinas.agents.{namespace}.{agent_name}.read:own"
    agent_perm_group = f"sinas.agents.{namespace}.{agent_name}.read:group"
    agent_perm_all = f"sinas.agents.{namespace}.{agent_name}.read:all"

    has_permission = (
        check_permission(permissions, agent_perm_all) or
        (check_permission(permissions, agent_perm_group) and agent.group_id) or
        (check_permission(permissions, agent_perm) and str(agent.user_id) == user_id)
    )

    if not has_permission:
        set_permission_used(http_request, agent_perm, has_perm=False)
        raise HTTPException(403, f"Not authorized to use agent '{namespace}/{agent_name}'")

    set_permission_used(http_request, agent_perm_all if check_permission(permissions, agent_perm_all) else agent_perm)

    # 3. Validate input data against agent's input_schema (if provided)
    if request.input and agent.input_schema:
        try:
            jsonschema.validate(request.input, agent.input_schema)
        except jsonschema.ValidationError as e:
            raise HTTPException(400, f"Input validation failed: {e.message}")

    # 4. Create chat
    chat = Chat(
        user_id=user_id,
        group_id=agent.group_id,
        agent_id=agent.id,
        agent_namespace=namespace,
        agent_name=agent_name,
        title=request.title or f"Chat with {namespace}/{agent_name}"
    )
    db.add(chat)
    await db.commit()
    await db.refresh(chat)

    return ChatResponse.model_validate(chat)


@router.post("/chats/{chat_id}/messages", response_model=MessageResponse)
async def send_message(
    chat_id: str,
    request: MessageSendRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions)
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

    # Verify ownership - only the chat owner can send messages
    if str(chat.user_id) != user_id:
        set_permission_used(http_request, "sinas.chats.write:own", has_perm=False)
        raise HTTPException(403, "Not authorized to send messages in this chat")

    set_permission_used(http_request, "sinas.chats.write:own")

    # Extract token for auth
    user_token = http_request.headers.get("authorization", "").replace("Bearer ", "")

    # Use message service
    message_service = MessageService(db)

    # Handle Union[str, List[Dict]] content - convert to string if needed
    content_str = request.content if isinstance(request.content, str) else json.dumps(request.content)

    response_message = await message_service.send_message(
        chat_id=str(chat.id),
        user_id=user_id,
        user_token=user_token,
        content=content_str
    )

    return MessageResponse.model_validate(response_message)


@router.post("/chats/{chat_id}/messages/stream")
async def stream_message(
    chat_id: str,
    request: MessageSendRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions)
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

    # Verify ownership - only the chat owner can send messages
    if str(chat.user_id) != user_id:
        set_permission_used(http_request, "sinas.chats.write:own", has_perm=False)
        raise HTTPException(403, "Not authorized to send messages in this chat")

    set_permission_used(http_request, "sinas.chats.write:own")

    # Extract token for auth
    user_token = http_request.headers.get("authorization", "").replace("Bearer ", "")

    # Use message service
    message_service = MessageService(db)

    # Handle Union[str, List[Dict]] content - convert to string if needed
    content_str = request.content if isinstance(request.content, str) else json.dumps(request.content)

    async def event_generator():
        try:
            async for chunk in message_service.send_message_stream(
                chat_id=str(chat.id),
                user_id=user_id,
                user_token=user_token,
                content=content_str
            ):
                yield {
                    "event": "message",
                    "data": json.dumps(chunk)
                }

            yield {
                "event": "done",
                "data": json.dumps({"status": "completed"})
            }

        except Exception as e:
            yield {
                "event": "error",
                "data": json.dumps({"error": str(e)})
            }

    return EventSourceResponse(event_generator())


@router.get("/chats", response_model=List[ChatResponse])
async def list_chats(
    request: Request,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db)
):
    """List all chats for the current user."""
    user_id, permissions = current_user_data
    set_permission_used(request, "sinas.chats.get:own")

    result = await db.execute(
        select(Chat).where(Chat.user_id == user_id).order_by(Chat.updated_at.desc())
    )
    chats = result.scalars().all()

    return [ChatResponse.model_validate(chat) for chat in chats]


@router.get("/chats/{chat_id}", response_model=ChatWithMessages)
async def get_chat(
    request: Request,
    chat_id: str,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db)
):
    """Get a chat with all messages."""
    user_id, permissions = current_user_data
    set_permission_used(request, "sinas.chats.get:own")

    result = await db.execute(
        select(Chat).where(Chat.id == chat_id, Chat.user_id == user_id)
    )
    chat = result.scalar_one_or_none()

    if not chat:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat not found"
        )

    # Get messages
    result = await db.execute(
        select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at)
    )
    messages = result.scalars().all()

    return ChatWithMessages(
        **ChatResponse.model_validate(chat).model_dump(),
        messages=[MessageResponse.model_validate(msg) for msg in messages]
    )


@router.put("/chats/{chat_id}", response_model=ChatResponse)
async def update_chat(
    chat_id: str,
    request: ChatUpdate,
    http_request: Request,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db)
):
    """Update a chat."""
    user_id, permissions = current_user_data
    set_permission_used(http_request, "sinas.chats.put:own")

    result = await db.execute(
        select(Chat).where(Chat.id == chat_id, Chat.user_id == user_id)
    )
    chat = result.scalar_one_or_none()

    if not chat:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat not found"
        )

    if request.title is not None:
        chat.title = request.title

    await db.commit()
    await db.refresh(chat)

    return ChatResponse.model_validate(chat)


@router.delete("/chats/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat(
    chat_id: str,
    http_request: Request,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db)
):
    """Delete a chat and all its messages."""
    user_id, permissions = current_user_data
    set_permission_used(http_request, "sinas.chats.delete:own")

    result = await db.execute(
        select(Chat).where(Chat.id == chat_id, Chat.user_id == user_id)
    )
    chat = result.scalar_one_or_none()

    if not chat:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat not found"
        )

    await db.delete(chat)
    await db.commit()

    return None
