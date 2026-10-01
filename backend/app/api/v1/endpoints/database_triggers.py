"""Database Triggers API endpoints for CDC.

Writes go through DatabaseTriggerApplier — the same path config apply and
package install use — so validation, ownership, CDC notifications and change
history are identical on every channel.
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models.database_connection import DatabaseConnection
from app.models.database_trigger import DatabaseTrigger
from app.schemas.database_trigger import (
    DatabaseTriggerCreate,
    DatabaseTriggerResponse,
    DatabaseTriggerUpdate,
)
from app.schemas.spec.database_trigger import DatabaseTriggerSpec
from app.services.resources import ApplierError, ApplyContext
from app.services.resources.database_triggers import DatabaseTriggerApplier
from app.services.resources.patch import PatchRejected, patched_spec

router = APIRouter(prefix="/database-triggers", tags=["database-triggers"])

_applier = DatabaseTriggerApplier()


def _context(db: AsyncSession, user_id, owner_id) -> ApplyContext:
    return ApplyContext(
        db=db,
        origin="api",
        actor_user_id=str(user_id),
        # Names are unique per owner, so an edit is checked against the
        # trigger owner's other triggers, and its function must be theirs:
        # the trigger runs as its owner.
        owner_user_id=str(owner_id),
        reference_scope_user_id=str(owner_id),
    )


def _spec(data: dict) -> DatabaseTriggerSpec:
    try:
        return DatabaseTriggerSpec.model_validate(data)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=json.loads(e.json(include_url=False)))


async def _commit_and_notify(db: AsyncSession, ctx: ApplyContext) -> None:
    """The CDC worker hears about a change only once it is durable. Deletes
    used to be announced before they were even flushed, so a failed commit
    stopped a trigger that still existed."""
    await db.commit()
    await ctx.effects.flush()


async def _resolve_trigger(
    db: AsyncSession, name: str, user_id, has_all: bool, lock: bool = False
):
    """Resolve a trigger by name for this caller.

    Trigger names are unique per (user_id, name), NOT globally — so selecting on
    the name alone raised MultipleResultsFound (a 500) the moment two users
    picked the same name, e.g. "daily-sync". Scope to the caller unless they
    hold the :all permission, and prefer their own row when several exist.
    Scoping also means another user's trigger reads as 404 rather than 403, so
    the endpoint stops disclosing which names exist.
    """
    query = select(DatabaseTrigger).where(DatabaseTrigger.name == name)
    if not has_all:
        query = query.where(DatabaseTrigger.user_id == user_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    rows = (await db.execute(query)).scalars().all()
    if not rows:
        return None
    return next((t for t in rows if str(t.user_id) == str(user_id)), rows[0])


@router.post("", response_model=DatabaseTriggerResponse, status_code=status.HTTP_201_CREATED)
async def create_database_trigger(
    request: Request,
    trigger_data: DatabaseTriggerCreate,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Create a new database trigger."""
    user_id, permissions = current_user_data

    create_perm = "sinas.database_triggers.create:own"
    if not check_permission(permissions, create_perm):
        set_permission_used(request, create_perm, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to create database triggers")
    set_permission_used(request, create_perm)

    # The API names the connection by id, and only an active one will do;
    # the spec (shared with config) names it.
    connection_name = (
        await db.execute(
            select(DatabaseConnection.name).where(
                DatabaseConnection.id == trigger_data.database_connection_id,
                DatabaseConnection.is_active == True,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if connection_name is None:
        raise HTTPException(status_code=404, detail="Database connection not found or inactive")

    data = trigger_data.model_dump()
    target_type = data["target_type"]
    spec = _spec({
        **{key: value for key, value in data.items() if key in DatabaseTriggerSpec.model_fields},
        "connection_name": connection_name,
        "target_namespace": data[f"{target_type}_namespace"],
        "target_name": data[f"{target_type}_name"],
    })

    ctx = _context(db, user_id, user_id)
    try:
        result = await _applier.apply(spec, ctx, must_create=True)
    except ApplierError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))

    await _commit_and_notify(db, ctx)
    await db.refresh(result.obj)
    return DatabaseTriggerResponse.model_validate(result.obj)


@router.get("", response_model=list[DatabaseTriggerResponse])
async def list_database_triggers(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """List database triggers."""
    user_id, permissions = current_user_data

    if check_permission(permissions, "sinas.database_triggers.read:all"):
        set_permission_used(request, "sinas.database_triggers.read:all")
        query = select(DatabaseTrigger)
    else:
        set_permission_used(request, "sinas.database_triggers.read:own")
        query = select(DatabaseTrigger).where(DatabaseTrigger.user_id == user_id)

    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    triggers = result.scalars().all()

    return [DatabaseTriggerResponse.model_validate(t) for t in triggers]


@router.get("/{name}", response_model=DatabaseTriggerResponse)
async def get_database_trigger(
    request: Request,
    name: str,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Get a specific database trigger by name."""
    user_id, permissions = current_user_data

    has_all = check_permission(permissions, "sinas.database_triggers.read:all")
    trigger = await _resolve_trigger(db, name, user_id, has_all)

    if not trigger:
        raise HTTPException(status_code=404, detail=f"Database trigger '{name}' not found")

    if check_permission(permissions, "sinas.database_triggers.read:all"):
        set_permission_used(request, "sinas.database_triggers.read:all")
    else:
        if trigger.user_id != user_id:
            set_permission_used(request, "sinas.database_triggers.read:own", has_perm=False)
            raise HTTPException(status_code=403, detail="Not authorized to view this trigger")
        set_permission_used(request, "sinas.database_triggers.read:own")

    return DatabaseTriggerResponse.model_validate(trigger)


@router.patch("/{name}", response_model=DatabaseTriggerResponse)
async def update_database_trigger(
    request: Request,
    name: str,
    trigger_data: DatabaseTriggerUpdate,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Update a database trigger."""
    user_id, permissions = current_user_data

    has_all = check_permission(permissions, "sinas.database_triggers.update:all")
    trigger = await _resolve_trigger(db, name, user_id, has_all, lock=True)
    if not trigger:
        raise HTTPException(status_code=404, detail=f"Database trigger '{name}' not found")
    set_permission_used(
        request,
        "sinas.database_triggers.update:all" if has_all else "sinas.database_triggers.update:own",
    )

    ctx = _context(db, user_id, trigger.user_id)
    stored = await _applier.current_spec(ctx, trigger)
    # The API never let a PATCH move a trigger to another connection, schema
    # or table (DatabaseTriggerUpdate has no such fields); that stays so.
    patch = {
        field: value
        for field, value in trigger_data.model_dump(exclude_unset=True).items()
        if value is not None and field in DatabaseTriggerSpec.model_fields
    }
    target_type = trigger_data.target_type or stored.target_type
    namespace = getattr(trigger_data, f"{target_type}_namespace")
    target_name = getattr(trigger_data, f"{target_type}_name")
    if target_type != stored.target_type:
        # A type switch needs the new type's name: a reference stored for
        # another type used to be picked up silently, unchecked.
        patch.update(target_namespace=namespace or "default", target_name=target_name)
    else:
        if namespace is not None:
            patch["target_namespace"] = namespace
        if target_name is not None:
            patch["target_name"] = target_name

    try:
        spec = patched_spec(stored, patch)
        await _applier.apply(spec, ctx, existing=trigger)
    except PatchRejected as e:
        raise HTTPException(status_code=422, detail=e.detail)
    except ApplierError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))

    await _commit_and_notify(db, ctx)
    await db.refresh(trigger)
    return DatabaseTriggerResponse.model_validate(trigger)


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_database_trigger(
    request: Request,
    name: str,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Delete a database trigger."""
    user_id, permissions = current_user_data

    has_all = check_permission(permissions, "sinas.database_triggers.delete:all")
    trigger = await _resolve_trigger(db, name, user_id, has_all, lock=True)
    if not trigger:
        raise HTTPException(status_code=404, detail=f"Database trigger '{name}' not found")
    set_permission_used(
        request,
        "sinas.database_triggers.delete:all" if has_all else "sinas.database_triggers.delete:own",
    )

    ctx = _context(db, user_id, trigger.user_id)
    await _applier.delete(trigger, ctx)
    await _commit_and_notify(db, ctx)
    return None
