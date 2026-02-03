"""State Store API endpoints."""
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models.state import State
from app.schemas import StateCreate, StateResponse, StateUpdate

router = APIRouter(prefix="/states")


@router.post("", response_model=StateResponse)
async def create_state(
    request: Request,
    state_data: StateCreate,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """
    Create a new state entry.

    Requires namespace-based permission: sinas.states/{namespace}.create:own
    """
    user_id, permissions = current_user_data
    user_uuid = uuid.UUID(user_id)

    # Check namespace permission
    namespace_perm = f"sinas.states/{state_data.namespace}.create:own"
    namespace_perm_all = f"sinas.states/{state_data.namespace}.create:all"

    if check_permission(permissions, namespace_perm_all):
        set_permission_used(request, namespace_perm_all)
    elif check_permission(permissions, namespace_perm):
        set_permission_used(request, namespace_perm)
    else:
        set_permission_used(request, namespace_perm, has_perm=False)
        raise HTTPException(
            status_code=403,
            detail=f"Not authorized to create states in namespace '{state_data.namespace}'",
        )

    # Check if state with same user_id, namespace, and key already exists
    result = await db.execute(
        select(State).where(
            and_(
                State.user_id == user_uuid,
                State.namespace == state_data.namespace,
                State.key == state_data.key,
            )
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail=f"State with namespace '{state_data.namespace}' and key '{state_data.key}' already exists",
        )

    # Create state
    state = State(
        user_id=user_uuid,
        namespace=state_data.namespace,
        key=state_data.key,
        value=state_data.value,
        visibility=state_data.visibility,
        description=state_data.description,
        tags=state_data.tags,
        relevance_score=state_data.relevance_score,
        expires_at=state_data.expires_at,
    )

    db.add(state)
    await db.commit()
    await db.refresh(state)

    return state


@router.get("", response_model=list[StateResponse])
async def list_states(
    request: Request,
    namespace: Optional[str] = None,
    visibility: Optional[str] = Query(None, pattern=r"^(private|shared)$"),
    tags: Optional[str] = Query(None, description="Comma-separated list of tags"),
    search: Optional[str] = Query(None, description="Search in keys and descriptions"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """
    List states accessible to the current user.

    Returns:
    - Own states (always)
    - Shared states in namespaces where user has read:all permission
    """
    user_id, permissions = current_user_data
    user_uuid = uuid.UUID(user_id)

    # Build base query: own states OR (shared + has namespace permission)
    # We'll filter by namespace permission in Python since we can't do dynamic permission checks in SQL
    query = select(State).where(
        and_(or_(State.expires_at == None, State.expires_at > datetime.utcnow()))
    )

    # Add filters
    if namespace:
        query = query.where(State.namespace == namespace)
    if visibility:
        query = query.where(State.visibility == visibility)
    if tags:
        tag_list = [t.strip() for t in tags.split(",")]
        query = query.where(State.tags.contains(tag_list))
    if search:
        query = query.where(
            or_(State.key.ilike(f"%{search}%"), State.description.ilike(f"%{search}%"))
        )

    # Execute query
    result = await db.execute(query.offset(skip).limit(limit))
    all_states = result.scalars().all()

    # Filter based on permissions
    accessible_states = []
    for state in all_states:
        # Own state?
        if state.user_id == user_uuid:
            accessible_states.append(state)
            continue

        # Shared state with namespace permission?
        if state.visibility == "shared":
            namespace_perm_all = f"sinas.states/{state.namespace}.read:all"
            if check_permission(permissions, namespace_perm_all):
                accessible_states.append(state)

    # Set permission used (use generic if no namespace filter, specific if filtered)
    if namespace:
        perm = f"sinas.states/{namespace}.read:own"
        set_permission_used(request, perm)
    else:
        set_permission_used(request, "sinas.states.read:own")

    return accessible_states


@router.get("/{state_id}", response_model=StateResponse)
async def get_state(
    request: Request,
    state_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Get a specific state by ID."""
    user_id, permissions = current_user_data
    user_uuid = uuid.UUID(user_id)

    # Get state
    result = await db.execute(select(State).where(State.id == state_id))
    state = result.scalar_one_or_none()

    if not state:
        raise HTTPException(status_code=404, detail="State not found")

    # Check if expired
    if state.expires_at and state.expires_at <= datetime.utcnow():
        raise HTTPException(status_code=404, detail="State has expired")

    # Check permissions
    namespace_perm = f"sinas.states/{state.namespace}.read:own"
    namespace_perm_all = f"sinas.states/{state.namespace}.read:all"

    # Own state?
    if state.user_id == user_uuid:
        set_permission_used(request, namespace_perm)
        return state

    # Shared state with namespace permission?
    if state.visibility == "shared" and check_permission(permissions, namespace_perm_all):
        set_permission_used(request, namespace_perm_all)
        return state

    # No access
    set_permission_used(request, namespace_perm, has_perm=False)
    raise HTTPException(status_code=403, detail="Not authorized to view this state")


@router.put("/{state_id}", response_model=StateResponse)
async def update_state(
    request: Request,
    state_id: uuid.UUID,
    state_data: StateUpdate,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Update a state entry. Only owner can update."""
    user_id, permissions = current_user_data
    user_uuid = uuid.UUID(user_id)

    # Get state
    result = await db.execute(select(State).where(State.id == state_id))
    state = result.scalar_one_or_none()

    if not state:
        raise HTTPException(status_code=404, detail="State not found")

    # Check permissions - only owner can update
    namespace_perm = f"sinas.states/{state.namespace}.update:own"
    namespace_perm_all = f"sinas.states/{state.namespace}.update:all"

    if state.user_id != user_uuid:
        # Not the owner - check if admin with :all permission
        if not check_permission(permissions, namespace_perm_all):
            set_permission_used(request, namespace_perm, has_perm=False)
            raise HTTPException(status_code=403, detail="Not authorized to update this state")
        set_permission_used(request, namespace_perm_all)
    else:
        set_permission_used(request, namespace_perm)

    # Update fields
    if state_data.value is not None:
        state.value = state_data.value
    if state_data.description is not None:
        state.description = state_data.description
    if state_data.tags is not None:
        state.tags = state_data.tags
    if state_data.relevance_score is not None:
        state.relevance_score = state_data.relevance_score
    if state_data.expires_at is not None:
        state.expires_at = state_data.expires_at
    if state_data.visibility is not None:
        state.visibility = state_data.visibility

    await db.commit()
    await db.refresh(state)

    return state


@router.delete("/{state_id}")
async def delete_state(
    request: Request,
    state_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Delete a state entry. Only owner can delete."""
    user_id, permissions = current_user_data
    user_uuid = uuid.UUID(user_id)

    # Get state
    result = await db.execute(select(State).where(State.id == state_id))
    state = result.scalar_one_or_none()

    if not state:
        raise HTTPException(status_code=404, detail="State not found")

    # Check permissions - only owner can delete
    namespace_perm = f"sinas.states/{state.namespace}.delete:own"
    namespace_perm_all = f"sinas.states/{state.namespace}.delete:all"

    if state.user_id != user_uuid:
        # Not the owner - check if admin with :all permission
        if not check_permission(permissions, namespace_perm_all):
            set_permission_used(request, namespace_perm, has_perm=False)
            raise HTTPException(status_code=403, detail="Not authorized to delete this state")
        set_permission_used(request, namespace_perm_all)
    else:
        set_permission_used(request, namespace_perm)

    await db.delete(state)
    await db.commit()

    return {"message": f"State '{state.namespace}/{state.key}' deleted successfully"}
