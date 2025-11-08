"""Context Store API endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_
from typing import List, Optional
import uuid
from datetime import datetime

from app.core.database import get_db
from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.models.context_store import ContextStore
from app.models.user import GroupMember
from app.schemas import ContextStoreCreate, ContextStoreUpdate, ContextStoreResponse

router = APIRouter(prefix="/contexts", tags=["contexts"])


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


@router.post("", response_model=ContextStoreResponse)
async def create_context(
    request: Request,
    context_data: ContextStoreCreate,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Create a new context entry."""
    user_id, permissions = current_user_data
    user_uuid = uuid.UUID(user_id)

    # Check permissions based on visibility
    if context_data.visibility == "group":
        # Users with :all scope automatically get :group access via scope hierarchy
        if not check_permission(permissions, "sinas.contexts.create:group"):
            set_permission_used(request, "sinas.contexts.create:group", has_perm=False)
            raise HTTPException(status_code=403, detail="Not authorized to create group contexts")

        if context_data.group_id is None:
            raise HTTPException(status_code=400, detail="group_id is required for group visibility")

        # Verify user is member of the group
        user_groups = await get_user_group_ids(db, user_uuid)
        if context_data.group_id not in user_groups:
            raise HTTPException(status_code=403, detail="Not a member of the specified group")

        if permissions.get("sinas.contexts.create:all"):
            set_permission_used(request, "sinas.contexts.create:all")
        else:
            set_permission_used(request, "sinas.contexts.create:group")
    else:
        # Private context
        if permissions.get("sinas.contexts.create:all"):
            set_permission_used(request, "sinas.contexts.create:all")
        elif permissions.get("sinas.contexts.create:own"):
            set_permission_used(request, "sinas.contexts.create:own")
        else:
            set_permission_used(request, "sinas.contexts.create:own", has_perm=False)
            raise HTTPException(status_code=403, detail="Not authorized to create contexts")

    # Check if context with same user_id, namespace, and key already exists
    result = await db.execute(
        select(ContextStore).where(
            and_(
                ContextStore.user_id == user_uuid,
                ContextStore.namespace == context_data.namespace,
                ContextStore.key == context_data.key
            )
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail=f"Context with namespace '{context_data.namespace}' and key '{context_data.key}' already exists"
        )

    # Create context
    context = ContextStore(
        user_id=user_uuid,
        group_id=context_data.group_id,
        assistant_id=context_data.assistant_id,
        namespace=context_data.namespace,
        key=context_data.key,
        value=context_data.value,
        visibility=context_data.visibility,
        description=context_data.description,
        tags=context_data.tags,
        relevance_score=context_data.relevance_score,
        expires_at=context_data.expires_at
    )

    db.add(context)
    await db.commit()
    await db.refresh(context)

    return context


@router.get("", response_model=List[ContextStoreResponse])
async def list_contexts(
    request: Request,
    namespace: Optional[str] = None,
    visibility: Optional[str] = Query(None, pattern=r'^(private|group|public)$'),
    assistant_id: Optional[uuid.UUID] = None,
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
    if permissions.get("sinas.contexts.read:all"):
        set_permission_used(request, "sinas.contexts.read:all")
        # Admin - see all non-expired contexts
        query = select(ContextStore).where(
            or_(
                ContextStore.expires_at == None,
                ContextStore.expires_at > datetime.utcnow()
            )
        )
    elif permissions.get("sinas.contexts.read:group"):
        set_permission_used(request, "sinas.contexts.read:group")
        # Can see own contexts and group contexts they have access to
        user_groups = await get_user_group_ids(db, user_uuid)
        query = select(ContextStore).where(
            and_(
                or_(
                    ContextStore.expires_at == None,
                    ContextStore.expires_at > datetime.utcnow()
                ),
                or_(
                    ContextStore.user_id == user_uuid,
                    and_(
                        ContextStore.visibility == "group",
                        ContextStore.group_id.in_(user_groups) if user_groups else False
                    )
                )
            )
        )
    else:
        set_permission_used(request, "sinas.contexts.read:own")
        # Own contexts only
        query = select(ContextStore).where(
            and_(
                ContextStore.user_id == user_uuid,
                or_(
                    ContextStore.expires_at == None,
                    ContextStore.expires_at > datetime.utcnow()
                )
            )
        )

    # Apply filters
    if namespace:
        query = query.where(ContextStore.namespace == namespace)

    if visibility:
        query = query.where(ContextStore.visibility == visibility)

    if assistant_id:
        query = query.where(ContextStore.assistant_id == assistant_id)

    if tags:
        tag_list = [tag.strip() for tag in tags.split(',')]
        for tag in tag_list:
            query = query.where(ContextStore.tags.contains([tag]))

    if search:
        search_pattern = f"%{search}%"
        query = query.where(
            or_(
                ContextStore.key.ilike(search_pattern),
                ContextStore.description.ilike(search_pattern)
            )
        )

    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    contexts = result.scalars().all()

    return contexts


@router.get("/{context_id}", response_model=ContextStoreResponse)
async def get_context(
    request: Request,
    context_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Get a specific context entry."""
    user_id, permissions = current_user_data
    user_uuid = uuid.UUID(user_id)

    result = await db.execute(
        select(ContextStore).where(ContextStore.id == context_id)
    )
    context = result.scalar_one_or_none()

    if not context:
        raise HTTPException(status_code=404, detail="Context not found")

    # Check if expired
    if context.expires_at and context.expires_at <= datetime.utcnow():
        raise HTTPException(status_code=404, detail="Context has expired")

    # Check permissions
    if permissions.get("sinas.contexts.read:all"):
        set_permission_used(request, "sinas.contexts.read:all")
    elif context.user_id == user_uuid:
        # User owns the context
        if permissions.get("sinas.contexts.read:own"):
            set_permission_used(request, "sinas.contexts.read:own")
        else:
            set_permission_used(request, "sinas.contexts.read:own", has_perm=False)
            raise HTTPException(status_code=403, detail="Not authorized to view this context")
    elif context.visibility == "group" and context.group_id:
        # Check if user is in the group
        user_groups = await get_user_group_ids(db, user_uuid)
        if context.group_id in user_groups:
            if permissions.get("sinas.contexts.read:group"):
                set_permission_used(request, "sinas.contexts.read:group")
            else:
                set_permission_used(request, "sinas.contexts.read:group", has_perm=False)
                raise HTTPException(status_code=403, detail="Not authorized to view this context")
        else:
            set_permission_used(request, "sinas.contexts.read:own", has_perm=False)
            raise HTTPException(status_code=403, detail="Not authorized to view this context")
    else:
        set_permission_used(request, "sinas.contexts.read:own", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to view this context")

    return context


@router.put("/{context_id}", response_model=ContextStoreResponse)
async def update_context(
    request: Request,
    context_id: uuid.UUID,
    context_data: ContextStoreUpdate,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Update a context entry."""
    user_id, permissions = current_user_data
    user_uuid = uuid.UUID(user_id)

    result = await db.execute(
        select(ContextStore).where(ContextStore.id == context_id)
    )
    context = result.scalar_one_or_none()

    if not context:
        raise HTTPException(status_code=404, detail="Context not found")

    # Check permissions
    can_update = False
    if permissions.get("sinas.contexts.update:all"):
        set_permission_used(request, "sinas.contexts.update:all")
        can_update = True
    elif context.user_id == user_uuid:
        # User owns the context
        if permissions.get("sinas.contexts.update:own"):
            set_permission_used(request, "sinas.contexts.update:own")
            can_update = True
    elif context.visibility == "group" and context.group_id:
        # Check if user is in the group
        user_groups = await get_user_group_ids(db, user_uuid)
        if context.group_id in user_groups:
            if permissions.get("sinas.contexts.update:group"):
                set_permission_used(request, "sinas.contexts.update:group")
                can_update = True

    if not can_update:
        set_permission_used(request, "sinas.contexts.update:own", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to update this context")

    # Update fields
    if context_data.value is not None:
        context.value = context_data.value
    if context_data.description is not None:
        context.description = context_data.description
    if context_data.tags is not None:
        context.tags = context_data.tags
    if context_data.relevance_score is not None:
        context.relevance_score = context_data.relevance_score
    if context_data.expires_at is not None:
        context.expires_at = context_data.expires_at
    if context_data.visibility is not None:
        # If changing to group visibility, verify group_id exists
        if context_data.visibility == "group" and not context.group_id:
            raise HTTPException(
                status_code=400,
                detail="Cannot change to group visibility without a group_id"
            )
        context.visibility = context_data.visibility

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
    """Delete a context entry."""
    user_id, permissions = current_user_data
    user_uuid = uuid.UUID(user_id)

    result = await db.execute(
        select(ContextStore).where(ContextStore.id == context_id)
    )
    context = result.scalar_one_or_none()

    if not context:
        raise HTTPException(status_code=404, detail="Context not found")

    # Check permissions
    can_delete = False
    if permissions.get("sinas.contexts.delete:all"):
        set_permission_used(request, "sinas.contexts.delete:all")
        can_delete = True
    elif context.user_id == user_uuid:
        # User owns the context
        if permissions.get("sinas.contexts.delete:own"):
            set_permission_used(request, "sinas.contexts.delete:own")
            can_delete = True
    elif context.visibility == "group" and context.group_id:
        # Check if user is in the group
        user_groups = await get_user_group_ids(db, user_uuid)
        if context.group_id in user_groups:
            if permissions.get("sinas.contexts.delete:group"):
                set_permission_used(request, "sinas.contexts.delete:group")
                can_delete = True

    if not can_delete:
        set_permission_used(request, "sinas.contexts.delete:own", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to delete this context")

    await db.delete(context)
    await db.commit()

    return {"message": f"Context '{context.namespace}/{context.key}' deleted successfully"}
