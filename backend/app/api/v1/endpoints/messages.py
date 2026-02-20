"""Messages API endpoints for analytics and insights."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models import Chat, Message, User

router = APIRouter(prefix="/messages", tags=["messages"])


@router.get("")
async def list_messages(
    request: Request,
    agent: Optional[str] = Query(None, description="Filter by agent (namespace/name)"),
    role: Optional[str] = Query(None, description="Filter by role (user/assistant/tool/system)"),
    search: Optional[str] = Query(None, description="Search in content"),
    limit: int = Query(100, ge=1, le=1000, description="Max messages to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db),
):
    """
    List messages with filters for analytics/insights.

    Permissions:
    - sinas.executions.read:all - View all messages
    - sinas.executions.read:own - View only own messages
    """
    user_id, permissions = current_user_data

    # Check permissions
    has_all_permission = check_permission(permissions, "sinas.executions.read:all")
    has_own_permission = check_permission(permissions, "sinas.executions.read:own")

    if not has_all_permission and not has_own_permission:
        set_permission_used(request, "sinas.executions.read:own", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to view messages")

    # Build query
    query = select(Message).join(Chat, Message.chat_id == Chat.id)

    # Apply permission-based filtering
    if not has_all_permission:
        # User can only see their own messages
        query = query.join(User, Chat.user_id == User.id).where(Chat.user_id == user_id)
        set_permission_used(request, "sinas.executions.read:own")
    else:
        # Admin can see all
        query = query.join(User, Chat.user_id == User.id, isouter=True)
        set_permission_used(request, "sinas.executions.read:all")

    # Apply filters
    if agent:
        if "/" in agent:
            namespace, name = agent.split("/", 1)
            query = query.where(and_(Chat.agent_namespace == namespace, Chat.agent_name == name))
        else:
            query = query.where(Chat.agent_name == agent)

    if role:
        query = query.where(Message.role == role)

    if search:
        query = query.where(Message.content.ilike(f"%{search}%"))

    # Order by newest first
    query = query.order_by(Message.created_at.desc())

    # Get total count
    count_query = select(func.count()).select_from(Message).join(Chat, Message.chat_id == Chat.id)
    if not has_all_permission:
        count_query = count_query.where(Chat.user_id == user_id)
    if agent:
        if "/" in agent:
            namespace, name = agent.split("/", 1)
            count_query = count_query.where(
                and_(Chat.agent_namespace == namespace, Chat.agent_name == name)
            )
    if role:
        count_query = count_query.where(Message.role == role)
    if search:
        count_query = count_query.where(Message.content.ilike(f"%{search}%"))

    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Apply pagination
    query = query.limit(limit).offset(offset)

    # Execute query
    result = await db.execute(query)
    messages = result.scalars().all()

    # Enrich with chat and user info
    enriched_messages = []
    for msg in messages:
        # Get chat details
        chat_result = await db.execute(select(Chat).where(Chat.id == msg.chat_id))
        chat = chat_result.scalar_one_or_none()

        # Get user details if chat exists
        user_email = None
        if chat and chat.user_id:
            user_result = await db.execute(select(User.email).where(User.id == chat.user_id))
            user_email = user_result.scalar_one_or_none()

        enriched_messages.append(
            {
                "id": str(msg.id),
                "chat_id": str(msg.chat_id),
                "role": msg.role,
                "content": msg.content,
                "tool_calls": msg.tool_calls,
                "tool_call_id": msg.tool_call_id,
                "created_at": msg.created_at.isoformat(),
                "chat": {
                    "agent_namespace": chat.agent_namespace if chat else None,
                    "agent_name": chat.agent_name if chat else None,
                }
                if chat
                else None,
                "user": {"email": user_email} if user_email else None,
            }
        )

    return {"messages": enriched_messages, "total": total, "limit": limit, "offset": offset}
