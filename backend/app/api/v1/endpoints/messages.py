"""Messages API endpoints for analytics and insights."""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import and_, cast, func, select, Date
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models import Chat, Message, User
from app.services.message_service import strip_base64_data

router = APIRouter(prefix="/messages", tags=["messages"])


@router.get("")
async def list_messages(
    request: Request,
    agent: Optional[str] = Query(None, description="Filter by agent (namespace/name)"),
    role: Optional[str] = Query(None, description="Filter by role (user/assistant/tool/system)"),
    search: Optional[str] = Query(None, description="Search in content"),
    limit: int = Query(50, ge=1, le=1000, description="Max messages to return"),
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

    # Execute query with chat and user info in a single query (avoid N+1)
    enriched_query = (
        query.add_columns(
            Chat.agent_namespace,
            Chat.agent_name,
            User.email.label("user_email"),
        )
    )

    result = await db.execute(enriched_query)
    rows = result.all()

    enriched_messages = []
    for row in rows:
        msg = row[0]  # Message object
        agent_namespace = row[1]
        agent_name = row[2]
        user_email = row[3]

        enriched_messages.append(
            {
                "id": str(msg.id),
                "chat_id": str(msg.chat_id),
                "role": msg.role,
                "content": strip_base64_data(msg.content),
                "tool_calls": msg.tool_calls,
                "tool_call_id": msg.tool_call_id,
                "created_at": msg.created_at.isoformat(),
                "chat": {
                    "agent_namespace": agent_namespace,
                    "agent_name": agent_name,
                },
                "user": {"email": user_email} if user_email else None,
            }
        )

    return {"messages": enriched_messages, "total": total, "limit": limit, "offset": offset}


@router.get("/stats")
async def get_message_stats(
    request: Request,
    days: int = Query(7, ge=1, le=90, description="Number of days to include"),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db),
):
    """
    Dashboard statistics — message counts, activity by day, agent usage, role distribution.
    Single query, no N+1.
    """
    user_id, permissions = current_user_data

    has_all = check_permission(permissions, "sinas.executions.read:all")
    has_own = check_permission(permissions, "sinas.executions.read:own")

    if not has_all and not has_own:
        set_permission_used(request, "sinas.executions.read:own", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to view message stats")

    set_permission_used(request, "sinas.executions.read:all" if has_all else "sinas.executions.read:own")

    since = datetime.now(timezone.utc) - timedelta(days=days)

    # Base filter
    base_filter = Message.created_at >= since
    if not has_all:
        base_filter = and_(base_filter, Chat.user_id == user_id)

    # Total messages
    total_q = select(func.count()).select_from(Message).join(Chat, Message.chat_id == Chat.id).where(base_filter)
    total = (await db.execute(total_q)).scalar() or 0

    # Tool call count
    tool_q = (
        select(func.count())
        .select_from(Message)
        .join(Chat, Message.chat_id == Chat.id)
        .where(and_(base_filter, Message.tool_calls.isnot(None)))
    )
    tool_calls = (await db.execute(tool_q)).scalar() or 0

    # Activity by day
    day_q = (
        select(
            cast(Message.created_at, Date).label("day"),
            func.count().label("count"),
        )
        .join(Chat, Message.chat_id == Chat.id)
        .where(base_filter)
        .group_by(cast(Message.created_at, Date))
        .order_by(cast(Message.created_at, Date))
    )
    day_rows = (await db.execute(day_q)).all()
    activity_by_day = {str(row.day): row.count for row in day_rows}

    # Agent usage
    agent_q = (
        select(
            Chat.agent_namespace,
            Chat.agent_name,
            func.count().label("count"),
        )
        .select_from(Message)
        .join(Chat, Message.chat_id == Chat.id)
        .where(base_filter)
        .group_by(Chat.agent_namespace, Chat.agent_name)
        .order_by(func.count().desc())
        .limit(10)
    )
    agent_rows = (await db.execute(agent_q)).all()
    agent_usage = [
        {"agent": f"{r.agent_namespace}/{r.agent_name}", "count": r.count}
        for r in agent_rows
    ]

    # Role distribution
    role_q = (
        select(Message.role, func.count().label("count"))
        .join(Chat, Message.chat_id == Chat.id)
        .where(base_filter)
        .group_by(Message.role)
    )
    role_rows = (await db.execute(role_q)).all()
    role_distribution = {r.role: r.count for r in role_rows}

    return {
        "total_messages": total,
        "tool_calls": tool_calls,
        "activity_by_day": activity_by_day,
        "agent_usage": agent_usage,
        "role_distribution": role_distribution,
        "days": days,
    }
