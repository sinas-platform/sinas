"""Database Triggers API endpoints for CDC."""

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.core.redis import get_redis
from app.models.database_connection import DatabaseConnection
from app.models.database_trigger import DatabaseTrigger
from app.models.function import Function
from app.schemas.database_trigger import (
    DatabaseTriggerCreate,
    DatabaseTriggerResponse,
    DatabaseTriggerUpdate,
)
from app.services.package_service import detach_if_package_managed

router = APIRouter(prefix="/database-triggers", tags=["database-triggers"])

CDC_CHANNEL = "sinas:cdc:triggers"


async def _notify_cdc(action: str, trigger_id: str) -> None:
    """Publish a trigger change event to the CDC service via Redis pub/sub."""
    redis = await get_redis()
    await redis.publish(CDC_CHANNEL, json.dumps({"action": action, "trigger_id": trigger_id}))


@router.post("", response_model=DatabaseTriggerResponse, status_code=status.HTTP_201_CREATED)
async def create_database_trigger(
    request: Request,
    trigger_data: DatabaseTriggerCreate,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Create a new database trigger for CDC polling."""
    user_id, permissions = current_user_data

    create_perm = "sinas.database_triggers.create:own"
    if not check_permission(permissions, create_perm):
        set_permission_used(request, create_perm, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to create database triggers")
    set_permission_used(request, create_perm)

    # Check unique name per user
    result = await db.execute(
        select(DatabaseTrigger).where(
            and_(DatabaseTrigger.user_id == user_id, DatabaseTrigger.name == trigger_data.name)
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=400, detail=f"Database trigger '{trigger_data.name}' already exists"
        )

    # Validate database connection exists and is active
    result = await db.execute(
        select(DatabaseConnection).where(
            and_(
                DatabaseConnection.id == trigger_data.database_connection_id,
                DatabaseConnection.is_active == True,
            )
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Database connection not found or inactive")

    # Validate function exists
    target_func = await Function.get_by_name(
        db, trigger_data.function_namespace, trigger_data.function_name, user_id
    )
    if not target_func:
        raise HTTPException(
            status_code=404,
            detail=f"Function '{trigger_data.function_namespace}/{trigger_data.function_name}' not found",
        )

    trigger = DatabaseTrigger(
        user_id=user_id,
        name=trigger_data.name,
        database_connection_id=trigger_data.database_connection_id,
        schema_name=trigger_data.schema_name,
        table_name=trigger_data.table_name,
        operations=trigger_data.operations,
        function_namespace=trigger_data.function_namespace,
        function_name=trigger_data.function_name,
        poll_column=trigger_data.poll_column,
        poll_interval_seconds=trigger_data.poll_interval_seconds,
        batch_size=trigger_data.batch_size,
    )

    db.add(trigger)
    await db.commit()
    await db.refresh(trigger)

    await _notify_cdc("add", str(trigger.id))

    return DatabaseTriggerResponse.model_validate(trigger)


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

    result = await db.execute(select(DatabaseTrigger).where(DatabaseTrigger.name == name))
    trigger = result.scalar_one_or_none()

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

    result = await db.execute(select(DatabaseTrigger).where(DatabaseTrigger.name == name))
    trigger = result.scalar_one_or_none()

    if not trigger:
        raise HTTPException(status_code=404, detail=f"Database trigger '{name}' not found")

    if check_permission(permissions, "sinas.database_triggers.update:all"):
        set_permission_used(request, "sinas.database_triggers.update:all")
    else:
        if trigger.user_id != user_id:
            set_permission_used(request, "sinas.database_triggers.update:own", has_perm=False)
            raise HTTPException(status_code=403, detail="Not authorized to update this trigger")
        set_permission_used(request, "sinas.database_triggers.update:own")

    detach_if_package_managed(trigger)

    if trigger_data.name is not None:
        trigger.name = trigger_data.name
    if trigger_data.operations is not None:
        trigger.operations = trigger_data.operations
    if trigger_data.function_namespace is not None:
        trigger.function_namespace = trigger_data.function_namespace
    if trigger_data.function_name is not None:
        trigger.function_name = trigger_data.function_name
    if trigger_data.poll_column is not None:
        trigger.poll_column = trigger_data.poll_column
    if trigger_data.poll_interval_seconds is not None:
        trigger.poll_interval_seconds = trigger_data.poll_interval_seconds
    if trigger_data.batch_size is not None:
        trigger.batch_size = trigger_data.batch_size
    if trigger_data.is_active is not None:
        trigger.is_active = trigger_data.is_active

    await db.commit()
    await db.refresh(trigger)

    await _notify_cdc("update", str(trigger.id))

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

    result = await db.execute(select(DatabaseTrigger).where(DatabaseTrigger.name == name))
    trigger = result.scalar_one_or_none()

    if not trigger:
        raise HTTPException(status_code=404, detail=f"Database trigger '{name}' not found")

    if check_permission(permissions, "sinas.database_triggers.delete:all"):
        set_permission_used(request, "sinas.database_triggers.delete:all")
    else:
        if trigger.user_id != user_id:
            set_permission_used(request, "sinas.database_triggers.delete:own", has_perm=False)
            raise HTTPException(status_code=403, detail="Not authorized to delete this trigger")
        set_permission_used(request, "sinas.database_triggers.delete:own")

    await _notify_cdc("remove", str(trigger.id))

    await db.delete(trigger)
    await db.flush()

    return None
