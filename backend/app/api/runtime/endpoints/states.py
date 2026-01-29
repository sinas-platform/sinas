"""State Store API endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_
from typing import List, Optional
import uuid
from datetime import datetime

from app.core.database import get_db
from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.permissions import check_permission
from app.models.state import State
from app.models.user import GroupMember
from app.schemas import StateCreate, StateUpdate, StateResponse

router = APIRouter(prefix="/states")


async def get_user_group_ids(db: AsyncSession, user_id: uuid.UUID) -> List[uuid.UUID]:
    """Get all group IDs that the user is a member of."""
    result = await db.execute(
        select(GroupMember.group_id).where(
            and_(
                GroupMember.user_id == user_id,
                GroupMember.active == True
            )
        )
    )
    return [row[0] for row in result.all()]


@router.post("", response_model=StateResponse)
async def create_state(
    request: Request,
    state_data: StateCreate,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Create a new state entry."""
    user_id, permissions = current_user_data
    user_uuid = uuid.UUID(user_id)

    # Check permissions based on visibility
    if state_data.visibility == "group":
        # Users with :all scope automatically get :group access via scope hierarchy
        if not check_permission(permissions, "sinas.contexts.post:group"):
            set_permission_used(request, "sinas.contexts.post:group", has_perm=False)
            raise HTTPException(status_code=403, detail="Not authorized to create group contexts")

        if state_data.group_id is None:
            raise HTTPException(status_code=400, detail="group_id is required for group visibility")

        # Verify user is member of the group
        user_groups = await get_user_group_ids(db, user_uuid)
        if state_data.group_id not in user_groups:
            raise HTTPException(status_code=403, detail="Not a member of the specified group")

        if check_permission(permissions,"sinas.contexts.post:all"):
            set_permission_used(request, "sinas.contexts.post:all")
        else:
            set_permission_used(request, "sinas.contexts.post:group")
    else:
        # Private context
        if check_permission(permissions,"sinas.contexts.post:all"):
            set_permission_used(request, "sinas.contexts.post:all")
        elif check_permission(permissions,"sinas.contexts.post:own"):
            set_permission_used(request, "sinas.contexts.post:own")
        else:
            set_permission_used(request, "sinas.contexts.post:own", has_perm=False)
            raise HTTPException(status_code=403, detail="Not authorized to create contexts")

    # Check if context with same user_id, namespace, and key already exists
    result = await db.execute(
        select(State).where(
            and_(
                State.user_id == user_uuid,
                State.namespace == state_data.namespace,
                State.key == state_data.key
            )
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail=f"Context with namespace '{state_data.namespace}' and key '{state_data.key}' already exists"
        )

    # Create context
    context = State(
        user_id=user_uuid,
        group_id=state_data.group_id,
        namespace=state_data.namespace,
        key=state_data.key,
        value=state_data.value,
        visibility=state_data.visibility,
        description=state_data.description,
        tags=state_data.tags,
        relevance_score=state_data.relevance_score,
        expires_at=state_data.expires_at
    )

    db.add(context)
    await db.commit()
    await db.refresh(context)

    return context


@router.get("", response_model=List[StateResponse])
async def list_contexts(
    request: Request,
    namespace: Optional[str] = None,
    visibility: Optional[str] = Query(None, pattern=r'^(private|group|public)$'),
    tags: Optional[str] = Query(None, description="Comma-separated list of tags"),
    search: Optional[str] = Query(None, description="Search in keys and descriptions"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """List contexts accessible to the current user."""
    user_id, permissions = current_user_data
    user_uuid = uuid.UUID(user_id)

    # Build base query based on permissions
    if check_permission(permissions,"sinas.contexts.get:all"):
        set_permission_used(request, "sinas.contexts.get:all")
        # Admin - see all non-expired contexts
        query = select(State).where(
            or_(
                State.expires_at == None,
                State.expires_at > datetime.utcnow()
            )
        )
    elif check_permission(permissions,"sinas.contexts.get:group"):
        set_permission_used(request, "sinas.contexts.get:group")
        # Can see own contexts and group contexts they have access to
        user_groups = await get_user_group_ids(db, user_uuid)
        query = select(State).where(
            and_(
                or_(
                    State.expires_at == None,
                    State.expires_at > datetime.utcnow()
                ),
                or_(
                    State.user_id == user_uuid,
                    and_(
                        State.visibility == "group",
                        State.group_id.in_(user_groups) if user_groups else False
                    )
                )
            )
        )
    else:
        set_permission_used(request, "sinas.contexts.get:own")
        # Own contexts only
        query = select(State).where(
            and_(
                State.user_id == user_uuid,
                or_(
                    State.expires_at == None,
                    State.expires_at > datetime.utcnow()
                )
            )
        )

    # Apply filters
    if namespace:
        query = query.where(State.namespace == namespace)

    if visibility:
        query = query.where(State.visibility == visibility)

    if tags:
        tag_list = [tag.strip() for tag in tags.split(',')]
        for tag in tag_list:
            query = query.where(State.tags.contains([tag]))

    if search:
        search_pattern = f"%{search}%"
        query = query.where(
            or_(
                State.key.ilike(search_pattern),
                State.description.ilike(search_pattern)
            )
        )

    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    contexts = result.scalars().all()

    return contexts


@router.get("/{context_id}", response_model=StateResponse)
async def get_context(
    request: Request,
    context_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Get a specific state entry."""
    user_id, permissions = current_user_data
    user_uuid = uuid.UUID(user_id)

    result = await db.execute(
        select(State).where(State.id == context_id)
    )
    context = result.scalar_one_or_none()

    if not context:
        raise HTTPException(status_code=404, detail="Context not found")

    # Check if expired
    if context.expires_at and context.expires_at <= datetime.utcnow():
        raise HTTPException(status_code=404, detail="Context has expired")

    # Check permissions
    if check_permission(permissions,"sinas.contexts.get:all"):
        set_permission_used(request, "sinas.contexts.get:all")
    elif context.user_id == user_uuid:
        # User owns the context
        if check_permission(permissions,"sinas.contexts.get:own"):
            set_permission_used(request, "sinas.contexts.get:own")
        else:
            set_permission_used(request, "sinas.contexts.get:own", has_perm=False)
            raise HTTPException(status_code=403, detail="Not authorized to view this context")
    elif context.visibility == "group" and context.group_id:
        # Check if user is in the group
        user_groups = await get_user_group_ids(db, user_uuid)
        if context.group_id in user_groups:
            if check_permission(permissions,"sinas.contexts.get:group"):
                set_permission_used(request, "sinas.contexts.get:group")
            else:
                set_permission_used(request, "sinas.contexts.get:group", has_perm=False)
                raise HTTPException(status_code=403, detail="Not authorized to view this context")
        else:
            set_permission_used(request, "sinas.contexts.get:own", has_perm=False)
            raise HTTPException(status_code=403, detail="Not authorized to view this context")
    else:
        set_permission_used(request, "sinas.contexts.get:own", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to view this context")

    return context


@router.put("/{context_id}", response_model=StateResponse)
async def update_context(
    request: Request,
    context_id: uuid.UUID,
    state_data: StateUpdate,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Update a state entry."""
    user_id, permissions = current_user_data
    user_uuid = uuid.UUID(user_id)

    result = await db.execute(
        select(State).where(State.id == context_id)
    )
    context = result.scalar_one_or_none()

    if not context:
        raise HTTPException(status_code=404, detail="Context not found")

    # Check permissions
    can_update = False
    if check_permission(permissions,"sinas.contexts.put:all"):
        set_permission_used(request, "sinas.contexts.put:all")
        can_update = True
    elif context.user_id == user_uuid:
        # User owns the context
        if check_permission(permissions,"sinas.contexts.put:own"):
            set_permission_used(request, "sinas.contexts.put:own")
            can_update = True
    elif context.visibility == "group" and context.group_id:
        # Check if user is in the group
        user_groups = await get_user_group_ids(db, user_uuid)
        if context.group_id in user_groups:
            if check_permission(permissions,"sinas.contexts.put:group"):
                set_permission_used(request, "sinas.contexts.put:group")
                can_update = True

    if not can_update:
        set_permission_used(request, "sinas.contexts.put:own", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to update this context")

    # Update fields
    if state_data.value is not None:
        context.value = state_data.value
    if state_data.description is not None:
        context.description = state_data.description
    if state_data.tags is not None:
        context.tags = state_data.tags
    if state_data.relevance_score is not None:
        context.relevance_score = state_data.relevance_score
    if state_data.expires_at is not None:
        context.expires_at = state_data.expires_at
    if state_data.visibility is not None:
        # If changing to group visibility, verify group_id exists
        if state_data.visibility == "group" and not context.group_id:
            raise HTTPException(
                status_code=400,
                detail="Cannot change to group visibility without a group_id"
            )
        context.visibility = state_data.visibility

    await db.commit()
    await db.refresh(context)

    return context


@router.delete("/{context_id}")
async def delete_context(
    request: Request,
    context_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Delete a state entry."""
    user_id, permissions = current_user_data
    user_uuid = uuid.UUID(user_id)

    result = await db.execute(
        select(State).where(State.id == context_id)
    )
    context = result.scalar_one_or_none()

    if not context:
        raise HTTPException(status_code=404, detail="Context not found")

    # Check permissions
    can_delete = False
    if check_permission(permissions,"sinas.contexts.delete:all"):
        set_permission_used(request, "sinas.contexts.delete:all")
        can_delete = True
    elif context.user_id == user_uuid:
        # User owns the context
        if check_permission(permissions,"sinas.contexts.delete:own"):
            set_permission_used(request, "sinas.contexts.delete:own")
            can_delete = True
    elif context.visibility == "group" and context.group_id:
        # Check if user is in the group
        user_groups = await get_user_group_ids(db, user_uuid)
        if context.group_id in user_groups:
            if check_permission(permissions,"sinas.contexts.delete:group"):
                set_permission_used(request, "sinas.contexts.delete:group")
                can_delete = True

    if not can_delete:
        set_permission_used(request, "sinas.contexts.delete:own", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to delete this context")

    await db.delete(context)
    await db.commit()

    return {"message": f"Context '{context.namespace}/{context.key}' deleted successfully"}
