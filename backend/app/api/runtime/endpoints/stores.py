"""Runtime state access within stores."""
import json
import uuid
from datetime import datetime
from typing import Optional

import jsonschema
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.encryption import encryption_service
from app.core.permissions import check_permission
from app.models.state import State
from app.models.store import Store
from app.schemas.state import StateCreate, StateResponse, StateUpdate

router = APIRouter(prefix="/stores", tags=["runtime-stores"])


def _encrypt_value(value: dict) -> str:
    return encryption_service.encrypt(json.dumps(value))


def _decrypt_value(encrypted_value: str) -> dict:
    return json.loads(encryption_service.decrypt(encrypted_value))


def _state_to_response(state: State, store: Store) -> StateResponse:
    value = state.value
    if state.encrypted and state.encrypted_value:
        value = _decrypt_value(state.encrypted_value)
    return StateResponse(
        id=state.id,
        user_id=state.user_id,
        store_id=state.store_id,
        store_namespace=store.namespace,
        store_name=store.name,
        key=state.key,
        value=value,
        visibility=state.visibility,
        encrypted=state.encrypted,
        description=state.description,
        tags=state.tags,
        relevance_score=state.relevance_score,
        expires_at=state.expires_at,
        created_at=state.created_at,
        updated_at=state.updated_at,
    )


async def _get_store(db: AsyncSession, namespace: str, name: str) -> Store:
    store = await Store.get_by_name(db, namespace, name)
    if not store:
        raise HTTPException(status_code=404, detail=f"Store '{namespace}/{name}' not found")
    return store


def _validate_against_schema(store: Store, key: str, value: dict):
    """Validate state value against store schema if strict."""
    if not store.strict or not store.schema:
        return

    schema = store.schema
    # Check if key is allowed
    if schema.get("properties") and key not in schema.get("properties", {}):
        raise HTTPException(
            status_code=400,
            detail=f"Key '{key}' is not allowed in strict store '{store.namespace}/{store.name}'. "
                   f"Allowed keys: {list(schema.get('properties', {}).keys())}",
        )

    # Validate value against the property schema if defined
    prop_schema = schema.get("properties", {}).get(key)
    if prop_schema:
        try:
            jsonschema.validate(instance=value, schema=prop_schema)
        except jsonschema.ValidationError as e:
            raise HTTPException(
                status_code=400,
                detail=f"Value validation failed for key '{key}': {e.message}",
            )


@router.post("/{namespace}/{name}/states", response_model=StateResponse)
async def create_state(
    namespace: str,
    name: str,
    request: Request,
    state_data: StateCreate,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Create a new state in a store."""
    user_id, permissions = current_user_data
    user_uuid = uuid.UUID(user_id)

    store = await _get_store(db, namespace, name)

    # Check permission
    perm = f"sinas.stores/{namespace}/{name}.write_state:own"
    if not check_permission(permissions, perm):
        set_permission_used(request, perm, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to write to this store")
    set_permission_used(request, perm)

    # Schema validation
    _validate_against_schema(store, state_data.key, state_data.value)

    # Uniqueness check
    result = await db.execute(
        select(State).where(
            and_(
                State.user_id == user_uuid,
                State.store_id == store.id,
                State.key == state_data.key,
            )
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail=f"State with key '{state_data.key}' already exists in store '{namespace}/{name}'",
        )

    # Determine encryption
    should_encrypt = state_data.encrypted or store.encrypted
    encrypted_value = None
    value = state_data.value
    if should_encrypt:
        encrypted_value = _encrypt_value(state_data.value)
        value = {}

    # Determine visibility
    visibility = state_data.visibility or store.default_visibility

    state = State(
        user_id=user_uuid,
        store_id=store.id,
        key=state_data.key,
        value=value,
        encrypted=should_encrypt,
        encrypted_value=encrypted_value,
        visibility=visibility,
        description=state_data.description,
        tags=state_data.tags,
        relevance_score=state_data.relevance_score,
        expires_at=state_data.expires_at,
    )

    db.add(state)
    await db.flush()
    await db.refresh(state)

    return _state_to_response(state, store)


@router.get("/{namespace}/{name}/states", response_model=list[StateResponse])
async def list_states(
    namespace: str,
    name: str,
    request: Request,
    search: Optional[str] = Query(None),
    tags: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """List states in a store."""
    user_id, permissions = current_user_data
    user_uuid = uuid.UUID(user_id)

    store = await _get_store(db, namespace, name)

    perm = f"sinas.stores/{namespace}/{name}.read_state:own"
    if not check_permission(permissions, perm):
        set_permission_used(request, perm, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to read this store")
    set_permission_used(request, perm)

    query = select(State).where(
        and_(
            State.store_id == store.id,
            or_(State.expires_at == None, State.expires_at > datetime.utcnow()),
        )
    )

    if search:
        query = query.where(
            or_(State.key.ilike(f"%{search}%"), State.description.ilike(f"%{search}%"))
        )
    if tags:
        tag_list = [t.strip() for t in tags.split(",")]
        query = query.where(State.tags.contains(tag_list))

    result = await db.execute(query.offset(skip).limit(limit))
    all_states = result.scalars().all()

    # Filter by visibility/ownership
    perm_all = f"sinas.stores/{namespace}/{name}.read_state:all"
    has_all = check_permission(permissions, perm_all)

    accessible = []
    for state in all_states:
        if state.user_id == user_uuid:
            accessible.append(state)
        elif has_all:
            accessible.append(state)
        elif state.visibility != "private":
            accessible.append(state)

    return [_state_to_response(s, store) for s in accessible]


@router.get("/{namespace}/{name}/states/{key}", response_model=StateResponse)
async def get_state(
    namespace: str,
    name: str,
    key: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Get a specific state by key."""
    user_id, permissions = current_user_data
    user_uuid = uuid.UUID(user_id)

    store = await _get_store(db, namespace, name)

    perm = f"sinas.stores/{namespace}/{name}.read_state:own"
    if not check_permission(permissions, perm):
        set_permission_used(request, perm, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to read this store")
    set_permission_used(request, perm)

    # Find state - try user's own first, then shared
    result = await db.execute(
        select(State).where(
            and_(
                State.store_id == store.id,
                State.key == key,
                State.user_id == user_uuid,
            )
        )
    )
    state = result.scalar_one_or_none()

    if not state:
        # Try finding a shared state
        result = await db.execute(
            select(State).where(
                and_(
                    State.store_id == store.id,
                    State.key == key,
                    State.visibility == "shared",
                )
            )
        )
        state = result.scalar_one_or_none()

    if not state:
        raise HTTPException(status_code=404, detail=f"State '{key}' not found in store '{namespace}/{name}'")

    # Check access
    if state.user_id != user_uuid:
        perm_all = f"sinas.stores/{namespace}/{name}.read_state:all"
        if state.visibility == "private" and not check_permission(permissions, perm_all):
            raise HTTPException(status_code=403, detail="Not authorized to read this state")

    return _state_to_response(state, store)


@router.put("/{namespace}/{name}/states/{key}", response_model=StateResponse)
async def update_state(
    namespace: str,
    name: str,
    key: str,
    request: Request,
    state_data: StateUpdate,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Update a state in a store."""
    user_id, permissions = current_user_data
    user_uuid = uuid.UUID(user_id)

    store = await _get_store(db, namespace, name)

    perm = f"sinas.stores/{namespace}/{name}.write_state:own"
    if not check_permission(permissions, perm):
        set_permission_used(request, perm, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to write to this store")
    set_permission_used(request, perm)

    result = await db.execute(
        select(State).where(
            and_(
                State.store_id == store.id,
                State.key == key,
                State.user_id == user_uuid,
            )
        )
    )
    state = result.scalar_one_or_none()

    if not state:
        raise HTTPException(status_code=404, detail=f"State '{key}' not found")

    # Validate new value if provided
    if state_data.value is not None:
        _validate_against_schema(store, key, state_data.value)

    # Handle encryption
    should_encrypt = store.encrypted or state.encrypted
    if state_data.encrypted is not None:
        should_encrypt = state_data.encrypted or store.encrypted

    turning_on = should_encrypt and not state.encrypted
    turning_off = not should_encrypt and state.encrypted and not store.encrypted

    if turning_on:
        plain_value = state_data.value if state_data.value is not None else state.value
        state.encrypted = True
        state.encrypted_value = _encrypt_value(plain_value)
        state.value = {}
    elif turning_off:
        plain_value = state_data.value
        if plain_value is None and state.encrypted_value:
            plain_value = _decrypt_value(state.encrypted_value)
        state.encrypted = False
        state.encrypted_value = None
        state.value = plain_value or {}
    else:
        if state_data.value is not None:
            if state.encrypted:
                state.encrypted_value = _encrypt_value(state_data.value)
                state.value = {}
            else:
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

    await db.flush()
    await db.refresh(state)

    return _state_to_response(state, store)


@router.delete("/{namespace}/{name}/states/{key}")
async def delete_state(
    namespace: str,
    name: str,
    key: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Delete a state from a store."""
    user_id, permissions = current_user_data
    user_uuid = uuid.UUID(user_id)

    store = await _get_store(db, namespace, name)

    perm = f"sinas.stores/{namespace}/{name}.write_state:own"
    if not check_permission(permissions, perm):
        set_permission_used(request, perm, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to write to this store")
    set_permission_used(request, perm)

    result = await db.execute(
        select(State).where(
            and_(
                State.store_id == store.id,
                State.key == key,
                State.user_id == user_uuid,
            )
        )
    )
    state = result.scalar_one_or_none()

    if not state:
        raise HTTPException(status_code=404, detail=f"State '{key}' not found")

    await db.delete(state)
    await db.flush()

    return {"message": f"State '{key}' deleted from store '{namespace}/{name}'"}
