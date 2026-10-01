"""Schedules API endpoints.

Writes go through ScheduleApplier — the same path config apply and package
install use — so validation, ownership, scheduler notifications and change
history are identical on every channel. Permission checks and lookups stay
here, at the API boundary.
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models.schedule import ScheduledJob
from app.schemas import ScheduledJobCreate, ScheduledJobResponse, ScheduledJobUpdate
from app.schemas.spec.schedule import ScheduleSpec
from app.services.resources import ApplierError, ApplyContext
from app.services.resources.schedules import ScheduleApplier

router = APIRouter(prefix="/schedules", tags=["schedules"])

_applier = ScheduleApplier()


def _context(db: AsyncSession, user_id) -> ApplyContext:
    return ApplyContext(
        db=db,
        origin="api",
        actor_user_id=str(user_id),
        owner_user_id=str(user_id),
        # The REST API has only ever let you schedule your own functions.
        reference_scope_user_id=str(user_id),
    )


def _spec(data: dict) -> ScheduleSpec:
    try:
        return ScheduleSpec.model_validate(data)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=json.loads(e.json(include_url=False)))


async def _commit_and_notify(db: AsyncSession, ctx: ApplyContext) -> None:
    """Effects fire only after the write is durable."""
    await db.commit()
    await ctx.effects.flush()


@router.post("", response_model=ScheduledJobResponse, status_code=status.HTTP_201_CREATED)
async def create_schedule(
    request: Request,
    schedule_data: ScheduledJobCreate,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Create a new scheduled job."""
    user_id, permissions = current_user_data

    # Check create permission
    create_perm = "sinas.schedules.create:own"
    if not check_permission(permissions, create_perm):
        set_permission_used(request, create_perm, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to create schedules")
    set_permission_used(request, create_perm)

    ctx = _context(db, user_id)
    try:
        result = await _applier.apply(
            _spec(schedule_data.model_dump()), ctx, must_create=True
        )
    except ApplierError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))

    await _commit_and_notify(db, ctx)
    await db.refresh(result.obj)
    return ScheduledJobResponse.model_validate(result.obj)


@router.get("", response_model=list[ScheduledJobResponse])
async def list_schedules(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """List scheduled jobs (own and group-accessible)."""
    user_id, permissions = current_user_data

    # Build query based on permissions
    if check_permission(permissions, "sinas.schedules.read:all"):
        set_permission_used(request, "sinas.schedules.read:all")
        query = select(ScheduledJob)
    else:
        set_permission_used(request, "sinas.schedules.read:own")
        query = select(ScheduledJob).where(ScheduledJob.user_id == user_id)

    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    schedules = result.scalars().all()

    return [ScheduledJobResponse.model_validate(schedule) for schedule in schedules]


@router.get("/{name}", response_model=ScheduledJobResponse)
async def get_schedule(
    request: Request,
    name: str,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Get a specific scheduled job."""
    user_id, permissions = current_user_data

    schedule = await ScheduledJob.get_by_name(db, name, user_id)

    if not schedule:
        raise HTTPException(status_code=404, detail=f"Schedule '{name}' not found")

    # Check permissions
    if check_permission(permissions, "sinas.schedules.read:all"):
        set_permission_used(request, "sinas.schedules.read:all")
    else:
        if schedule.user_id != user_id:
            set_permission_used(request, "sinas.schedules.read:own", has_perm=False)
            raise HTTPException(status_code=403, detail="Not authorized to view this schedule")
        set_permission_used(request, "sinas.schedules.read:own")

    response = ScheduledJobResponse.model_validate(schedule)

    return response


@router.patch("/{name}", response_model=ScheduledJobResponse)
async def update_schedule(
    request: Request,
    name: str,
    schedule_data: ScheduledJobUpdate,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Update a scheduled job."""
    user_id, permissions = current_user_data

    schedule = await ScheduledJob.get_by_name(db, name, user_id)

    if not schedule:
        raise HTTPException(status_code=404, detail=f"Schedule '{name}' not found")

    # Check permissions
    if check_permission(permissions, "sinas.schedules.update:all"):
        set_permission_used(request, "sinas.schedules.update:all")
    else:
        if schedule.user_id != user_id:
            set_permission_used(request, "sinas.schedules.update:own", has_perm=False)
            raise HTTPException(status_code=403, detail="Not authorized to update this schedule")
        set_permission_used(request, "sinas.schedules.update:own")

    # PATCH edits the resource's spec: merge the set fields over its current
    # state and apply the whole thing, so the result is validated as one spec
    # (a type change is checked against its new target, an agent schedule
    # still needs content). As before, fields sent as null are ignored.
    patch = {k: v for k, v in schedule_data.model_dump(exclude_unset=True).items() if v is not None}
    merged = {**_applier.spec_from_row(schedule).model_dump(), **patch}

    ctx = _context(db, user_id)
    try:
        await _applier.apply(_spec(merged), ctx, existing=schedule)
    except ApplierError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))

    await _commit_and_notify(db, ctx)
    await db.refresh(schedule)
    return ScheduledJobResponse.model_validate(schedule)


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(
    request: Request,
    name: str,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Delete a scheduled job."""
    user_id, permissions = current_user_data

    schedule = await ScheduledJob.get_by_name(db, name, user_id)

    if not schedule:
        raise HTTPException(status_code=404, detail=f"Schedule '{name}' not found")

    # Check permissions
    if check_permission(permissions, "sinas.schedules.delete:all"):
        set_permission_used(request, "sinas.schedules.delete:all")
    else:
        if schedule.user_id != user_id:
            set_permission_used(request, "sinas.schedules.delete:own", has_perm=False)
            raise HTTPException(status_code=403, detail="Not authorized to delete this schedule")
        set_permission_used(request, "sinas.schedules.delete:own")

    ctx = _context(db, user_id)
    await _applier.delete(schedule, ctx)
    # The scheduler used to be told before the delete was even flushed, so a
    # failed commit left it running a job that still existed.
    await _commit_and_notify(db, ctx)
    return None
