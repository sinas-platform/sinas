"""Chat and message endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
from sse_starlette.sse import EventSourceResponse
import json

from app.core.database import get_db
from app.core.auth import get_current_user, require_permission, verify_jwt_or_api_key, set_permission_used
from app.models import Chat, Message
from app.schemas.chat import (
    ChatCreate,
    ChatUpdate,
    ChatResponse,
    MessageResponse,
    MessageSendRequest,
    ChatWithMessages,
)
from app.services.message_service import MessageService

router = APIRouter()


@router.post("", response_model=ChatResponse, status_code=status.HTTP_201_CREATED)
async def create_chat(
    request: ChatCreate,
    user_id: str = Depends(require_permission("sinas.chats.create:own")),
    db: AsyncSession = Depends(get_db)
):
    """Create a new chat."""
    chat = Chat(
        user_id=user_id,
        group_id=request.group_id,
        assistant_id=request.assistant_id,
        title=request.title,
        enabled_webhooks=request.enabled_webhooks or [],
        enabled_mcp_tools=request.enabled_mcp_tools or []
    )

    db.add(chat)
    await db.commit()
    await db.refresh(chat)

    return ChatResponse.model_validate(chat)


@router.get("", response_model=List[ChatResponse])
async def list_chats(
    request: Request,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List all chats for the current user."""
    set_permission_used(request, "sinas.chats.read:own")

    result = await db.execute(
        select(Chat).where(Chat.user_id == user_id).order_by(Chat.updated_at.desc())
    )
    chats = result.scalars().all()

    return [ChatResponse.model_validate(chat) for chat in chats]


@router.get("/{chat_id}", response_model=ChatWithMessages)
async def get_chat(
    request: Request,
    chat_id: str,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get a chat with all messages."""
    set_permission_used(request, "sinas.chats.read:own")

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


@router.put("/{chat_id}", response_model=ChatResponse)
async def update_chat(
    chat_id: str,
    request: ChatUpdate,
    user_id: str = Depends(require_permission("sinas.chats.update:own")),
    db: AsyncSession = Depends(get_db)
):
    """Update a chat."""
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
    if request.enabled_webhooks is not None:
        chat.enabled_webhooks = request.enabled_webhooks
    if request.enabled_mcp_tools is not None:
        chat.enabled_mcp_tools = request.enabled_mcp_tools

    await db.commit()
    await db.refresh(chat)

    return ChatResponse.model_validate(chat)


@router.delete("/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat(
    chat_id: str,
    user_id: str = Depends(require_permission("sinas.chats.delete:own")),
    db: AsyncSession = Depends(get_db)
):
    """Delete a chat and all its messages."""
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


@router.post("/{chat_id}/messages", response_model=MessageResponse)
async def send_message(
    http_request: Request,
    chat_id: str,
    request: MessageSendRequest,
    auth_data: tuple = Depends(verify_jwt_or_api_key),
    db: AsyncSession = Depends(get_db)
):
    """Send a message and get LLM response."""
    user_id, email, permissions = auth_data
    set_permission_used(http_request, "sinas.chats.messages.create:own")

    # Extract token from Authorization header
    user_token = http_request.headers.get("authorization", "").replace("Bearer ", "")

    service = MessageService(db)

    try:
        response_message = await service.send_message(
            chat_id=chat_id,
            user_id=user_id,
            user_token=user_token,
            content=request.content,
            provider=request.provider,
            model=request.model,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            enabled_webhooks=request.enabled_webhooks,
            disabled_webhooks=request.disabled_webhooks,
            enabled_mcp_tools=request.enabled_mcp_tools,
            disabled_mcp_tools=request.disabled_mcp_tools,
            inject_context=request.inject_context,
            context_namespaces=request.context_namespaces,
            context_limit=request.context_limit
        )

        return MessageResponse.model_validate(response_message)

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )


@router.post("/{chat_id}/messages/stream")
async def stream_message(
    http_request: Request,
    chat_id: str,
    request: MessageSendRequest,
    auth_data: tuple = Depends(verify_jwt_or_api_key),
    db: AsyncSession = Depends(get_db)
):
    """Send a message and stream LLM response via SSE."""
    user_id, email, permissions = auth_data
    set_permission_used(http_request, "sinas.chats.messages.create:own")

    # Extract token from Authorization header
    user_token = http_request.headers.get("authorization", "").replace("Bearer ", "")

    service = MessageService(db)

    async def event_generator():
        try:
            async for chunk in service.stream_message(
                chat_id=chat_id,
                user_id=user_id,
                user_token=user_token,
                content=request.content,
                provider=request.provider,
                model=request.model,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                enabled_webhooks=request.enabled_webhooks,
                disabled_webhooks=request.disabled_webhooks,
                enabled_mcp_tools=request.enabled_mcp_tools,
                disabled_mcp_tools=request.disabled_mcp_tools,
                inject_context=request.inject_context,
                context_namespaces=request.context_namespaces,
                context_limit=request.context_limit
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


@router.get("/{chat_id}/messages", response_model=List[MessageResponse])
async def list_messages(
    request: Request,
    chat_id: str,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List all messages in a chat."""
    set_permission_used(request, "sinas.chats.messages.read:own")

    # Verify user owns the chat
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

    return [MessageResponse.model_validate(msg) for msg in messages]
