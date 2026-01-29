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
import asyncio
import logging
import traceback

from app.core.database import get_db, AsyncSessionLocal
from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.models.agent import Agent
from app.models.chat import Chat
from app.models import Message
from app.models.pending_approval import PendingToolApproval
from app.models.user import User
from sqlalchemy import func
from app.services.message_service import MessageService
from app.schemas.chat import AgentChatCreateRequest, MessageSendRequest, ChatResponse, MessageResponse, ChatUpdate, ChatWithMessages, ToolApprovalRequest, ToolApprovalResponse

logger = logging.getLogger(__name__)
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

    # 4. Create chat with input data stored in metadata
    chat = Chat(
        user_id=user_id,
        group_id=agent.group_id,
        agent_id=agent.id,
        agent_namespace=namespace,
        agent_name=agent_name,
        title=request.title or f"Chat with {namespace}/{agent_name}",
        chat_metadata={"agent_input": request.input} if request.input else None
    )
    db.add(chat)
    await db.commit()
    await db.refresh(chat)

    # 5. Pre-populate with initial_messages if present (rendered with input data)
    if agent.initial_messages:
        from app.services.template_renderer import render_template

        for msg_data in agent.initial_messages:
            # Render message content with input_data if it's a string
            content = msg_data["content"]
            if isinstance(content, str) and request.input:
                try:
                    content = render_template(content, request.input)
                except Exception as e:
                    logger.error(f"Failed to render initial message template: {e}")

            message = Message(
                chat_id=chat.id,
                role=msg_data["role"],
                content=content
            )
            db.add(message)
        await db.commit()

    # Get user email
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one()

    return ChatResponse(
        id=chat.id,
        user_id=chat.user_id,
        user_email=user.email,
        group_id=chat.group_id,
        agent_id=chat.agent_id,
        agent_namespace=chat.agent_namespace,
        agent_name=chat.agent_name,
        title=chat.title,
        created_at=chat.created_at,
        updated_at=chat.updated_at,
        last_message_at=None  # New chat has no messages yet
    )


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
    from fastapi import BackgroundTasks
    from app.core.database import AsyncSessionLocal

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

    # Track accumulated content for partial save
    accumulated_content = {"content": ""}

    async def event_generator():
        try:
            async for chunk in message_service.send_message_stream(
                chat_id=str(chat.id),
                user_id=user_id,
                user_token=user_token,
                content=content_str
            ):
                # Accumulate content BEFORE yielding
                if chunk.get("content"):
                    accumulated_content["content"] += chunk["content"]

                # Try to yield - this will raise exception if client disconnected
                try:
                    yield {
                        "event": "message",
                        "data": json.dumps(chunk)
                    }
                except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
                    # Client disconnected while yielding - save partial and exit
                    if accumulated_content["content"]:
                        try:
                            async with AsyncSessionLocal() as new_db:
                                partial_msg = Message(
                                    chat_id=str(chat.id),
                                    role="assistant",
                                    content=accumulated_content["content"],
                                    tool_calls=None
                                )
                                new_db.add(partial_msg)
                                await new_db.commit()
                        except Exception as save_error:
                            print(f"⚠️  Failed to save partial message: {save_error}")
                    return

            # Stream completed normally
            yield {
                "event": "done",
                "data": json.dumps({"status": "completed"})
            }

        except asyncio.CancelledError:
            # Request cancelled - save partial message (shielded from cancellation)
            if accumulated_content["content"]:
                try:
                    async def save_partial():
                        async with AsyncSessionLocal() as new_db:
                            partial_msg = Message(
                                chat_id=str(chat.id),
                                role="assistant",
                                content=accumulated_content["content"],
                                tool_calls=None
                            )
                            new_db.add(partial_msg)
                            await new_db.commit()

                    await asyncio.shield(save_partial())
                except Exception as save_error:
                    print(f"⚠️  Failed to save partial message: {save_error}")

        except Exception as e:
            yield {
                "event": "error",
                "data": json.dumps({"error": str(e)})
            }

    return EventSourceResponse(event_generator())


@router.post("/chats/{chat_id}/approve-tool/{tool_call_id}", response_model=ToolApprovalResponse)
async def approve_tool_call(
    chat_id: str,
    tool_call_id: str,
    request: ToolApprovalRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions)
):
    """
    Approve or reject a tool call that requires user approval.

    When a function with requires_approval=True is called by the LLM,
    execution pauses and an approval_required event is yielded.
    This endpoint allows the user to approve or reject the execution.

    - Loads pending approval by tool_call_id
    - Verifies chat ownership
    - Updates approval status
    - If approved, resumes execution
    - Returns result
    """
    user_id, permissions = current_user_data

    # Load chat
    chat = await db.get(Chat, chat_id)
    if not chat:
        raise HTTPException(404, "Chat not found")

    # Verify ownership
    if str(chat.user_id) != user_id:
        set_permission_used(http_request, "sinas.chats.write:own", has_perm=False)
        raise HTTPException(403, "Not authorized to approve tools in this chat")

    set_permission_used(http_request, "sinas.chats.write:own")

    # Load pending approval
    result = await db.execute(
        select(PendingToolApproval).where(
            PendingToolApproval.tool_call_id == tool_call_id,
            PendingToolApproval.chat_id == chat_id,
            PendingToolApproval.approved == None  # Only pending approvals
        )
    )
    pending_approval = result.scalar_one_or_none()

    if not pending_approval:
        raise HTTPException(404, "Pending approval not found or already processed")

    # Update approval status
    pending_approval.approved = request.approved
    await db.commit()

    if not request.approved:
        # Rejected - send error as tool result and let LLM respond
        user_token = http_request.headers.get("authorization", "").replace("Bearer ", "")
        message_service = MessageService(db)

        # Create error tool result
        error_message = f"Tool call rejected by user: {pending_approval.function_namespace}/{pending_approval.function_name}"
        tool_message = Message(
            chat_id=chat_id,
            role="tool",
            content=json.dumps({"error": error_message}),
            tool_call_id=tool_call_id,
            name=f"{pending_approval.function_namespace}__{pending_approval.function_name}"
        )
        db.add(tool_message)
        await db.commit()

        # Get LLM response to the rejection
        try:
            from app.providers.factory import create_provider
            from app.services.template_renderer import render_template

            # Rebuild conversation with rejection
            # First, add system prompt with template variables
            result_chat = await db.execute(
                select(Chat).where(Chat.id == chat_id)
            )
            chat = result_chat.scalar_one_or_none()

            updated_messages = []

            # Add system prompt from agent if exists
            if chat and chat.agent_id:
                result_agent = await db.execute(
                    select(Agent).where(Agent.id == chat.agent_id)
                )
                agent = result_agent.scalar_one_or_none()
                if agent and agent.system_prompt:
                    # Render system prompt with template variables from chat metadata
                    system_content = agent.system_prompt
                    if chat.chat_metadata and "agent_input" in chat.chat_metadata:
                        try:
                            system_content = render_template(
                                agent.system_prompt,
                                chat.chat_metadata["agent_input"]
                            )
                        except Exception as e:
                            logger.error(f"Failed to render system prompt template: {e}")

                    # Add output schema instruction if agent has one
                    if agent.output_schema and agent.output_schema.get("properties"):
                        schema_instruction = f"\n\nIMPORTANT: You must respond with valid JSON matching this exact schema:\n```json\n{json.dumps(agent.output_schema, indent=2)}\n```\nDo not include any text outside the JSON object."
                        system_content += schema_instruction

                    updated_messages.append({
                        "role": "system",
                        "content": system_content
                    })

            # Add chat messages
            result = await db.execute(
                select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at)
            )
            for msg in result.scalars().all():
                message_dict = {"role": msg.role}
                if msg.content:
                    message_dict["content"] = msg.content
                if msg.tool_calls:
                    message_dict["tool_calls"] = msg.tool_calls
                if msg.tool_call_id:
                    message_dict["tool_call_id"] = msg.tool_call_id
                if msg.name:
                    message_dict["name"] = msg.name
                updated_messages.append(message_dict)

            llm_provider = await create_provider(
                pending_approval.conversation_context.get("provider"),
                pending_approval.conversation_context.get("model"),
                db
            )

            # Get response from LLM about the rejection
            response = await llm_provider.complete(
                messages=updated_messages,
                model=pending_approval.conversation_context.get("model"),
                tools=None,
                temperature=pending_approval.conversation_context.get("temperature", 0.7),
                max_tokens=pending_approval.conversation_context.get("max_tokens")
            )

            # Save assistant's response
            final_message = Message(
                chat_id=chat_id,
                role="assistant",
                content=response.get("content", "")
            )
            db.add(final_message)
            await db.commit()

            return ToolApprovalResponse(
                status="rejected",
                tool_call_id=tool_call_id,
                message=f"Tool call rejected. LLM responded with message ID: {final_message.id}"
            )
        except Exception as e:
            logger.error(f"Failed to get LLM response after rejection: {e}")
            return ToolApprovalResponse(
                status="rejected",
                tool_call_id=tool_call_id,
                message=f"Tool call rejected but failed to get LLM response: {str(e)}"
            )

    # Approved - resume execution
    # Extract token for auth
    user_token = http_request.headers.get("authorization", "").replace("Bearer ", "")

    # Use message service to execute the tool calls
    message_service = MessageService(db)

    try:
        # Execute tool calls using stored context
        result_message = await message_service._handle_tool_calls(
            chat_id=chat_id,
            user_id=user_id,
            user_token=user_token,
            messages=pending_approval.conversation_context["messages"],
            tool_calls=pending_approval.all_tool_calls,
            provider=pending_approval.conversation_context.get("provider"),
            model=pending_approval.conversation_context.get("model"),
            temperature=pending_approval.conversation_context.get("temperature", 0.7),
            max_tokens=pending_approval.conversation_context.get("max_tokens"),
            tools=pending_approval.conversation_context.get("tools", [])  # Restore tools with metadata
        )

        return ToolApprovalResponse(
            status="approved",
            tool_call_id=tool_call_id,
            message=f"Tool call executed successfully. Result message ID: {result_message.id}"
        )

    except Exception as e:
        logger.error(f"Failed to execute approved tool call: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(500, f"Failed to execute approved tool call: {str(e)}")


@router.get("/chats", response_model=List[ChatResponse])
async def list_chats(
    request: Request,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db)
):
    """List all chats for the current user."""
    user_id, permissions = current_user_data
    set_permission_used(request, "sinas.chats.get:own")

    # Subquery for last message timestamp
    last_message_subq = (
        select(
            Message.chat_id,
            func.max(Message.created_at).label('last_message_at')
        )
        .group_by(Message.chat_id)
        .subquery()
    )

    # Join with User and last message subquery
    result = await db.execute(
        select(
            Chat,
            User.email,
            last_message_subq.c.last_message_at
        )
        .join(User, Chat.user_id == User.id)
        .outerjoin(last_message_subq, Chat.id == last_message_subq.c.chat_id)
        .where(Chat.user_id == user_id)
        .order_by(Chat.updated_at.desc())
    )
    rows = result.all()

    # Build response with user_email and last_message_at
    chats_response = []
    for chat, email, last_message_at in rows:
        chats_response.append(ChatResponse(
            id=chat.id,
            user_id=chat.user_id,
            user_email=email,
            group_id=chat.group_id,
            agent_id=chat.agent_id,
            agent_namespace=chat.agent_namespace,
            agent_name=chat.agent_name,
            title=chat.title,
            created_at=chat.created_at,
            updated_at=chat.updated_at,
            last_message_at=last_message_at
        ))

    return chats_response


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

    # Get chat with user email
    result = await db.execute(
        select(Chat, User.email)
        .join(User, Chat.user_id == User.id)
        .where(Chat.id == chat_id, Chat.user_id == user_id)
    )
    row = result.one_or_none()

    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat not found"
        )

    chat, user_email = row

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
        group_id=chat.group_id,
        agent_id=chat.agent_id,
        agent_namespace=chat.agent_namespace,
        agent_name=chat.agent_name,
        title=chat.title,
        created_at=chat.created_at,
        updated_at=chat.updated_at,
        last_message_at=last_message_at,
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

    # Get user email and last message timestamp
    user_result = await db.execute(select(User).where(User.id == chat.user_id))
    user = user_result.scalar_one()

    # Get last message timestamp
    last_msg_result = await db.execute(
        select(func.max(Message.created_at))
        .where(Message.chat_id == chat_id)
    )
    last_message_at = last_msg_result.scalar()

    return ChatResponse(
        id=chat.id,
        user_id=chat.user_id,
        user_email=user.email,
        group_id=chat.group_id,
        agent_id=chat.agent_id,
        agent_namespace=chat.agent_namespace,
        agent_name=chat.agent_name,
        title=chat.title,
        created_at=chat.created_at,
        updated_at=chat.updated_at,
        last_message_at=last_message_at
    )


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
