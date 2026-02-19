"""Schedules API endpoints."""

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.core.redis import get_redis
from app.models.schedule import ScheduledJob
from app.schemas import ScheduledJobCreate, ScheduledJobResponse, ScheduledJobUpdate

router = APIRouter(prefix="/schedules", tags=["schedules"])

SCHEDULER_CHANNEL = "sinas:scheduler:jobs"


async def _notify_scheduler(action: str, job_id: str) -> None:
    """Publish a job change event to the scheduler service via Redis pub/sub."""
    redis = await get_redis()
    await redis.publish(SCHEDULER_CHANNEL, json.dumps({"action": action, "job_id": job_id}))


@router.post("", response_model=ScheduledJobResponse)
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

    # Check if schedule name already exists for this user
    result = await db.execute(
        select(ScheduledJob).where(
            and_(ScheduledJob.user_id == user_id, ScheduledJob.name == schedule_data.name)
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=400, detail=f"Schedule '{schedule_data.name}' already exists"
        )

    # Verify target exists based on schedule_type
    if schedule_data.schedule_type == "function":
        from app.models.function import Function

        target = await Function.get_by_name(
            db, schedule_data.target_namespace, schedule_data.target_name, user_id
        )
        if not target:
            raise HTTPException(
                status_code=404,
                detail=f"Function '{schedule_data.target_namespace}/{schedule_data.target_name}' not found",
            )
    else:
        from app.models.agent import Agent

        result = await db.execute(
            select(Agent).where(
                and_(
                    Agent.namespace == schedule_data.target_namespace,
                    Agent.name == schedule_data.target_name,
                )
            )
        )
        if not result.scalar_one_or_none():
            raise HTTPException(
                status_code=404,
                detail=f"Agent '{schedule_data.target_namespace}/{schedule_data.target_name}' not found",
            )

    # Create schedule
    schedule = ScheduledJob(
        user_id=user_id,
        name=schedule_data.name,
        schedule_type=schedule_data.schedule_type,
        target_namespace=schedule_data.target_namespace,
        target_name=schedule_data.target_name,
        description=schedule_data.description,
        cron_expression=schedule_data.cron_expression,
        timezone=schedule_data.timezone,
        input_data=schedule_data.input_data,
        content=schedule_data.content,
    )

    db.add(schedule)
    await db.commit()
    await db.refresh(schedule)

    await _notify_scheduler("add", str(schedule.id))

    response = ScheduledJobResponse.model_validate(schedule)

    return response


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

    # Update fields
    if schedule_data.name is not None:
        schedule.name = schedule_data.name
    if schedule_data.schedule_type is not None:
        schedule.schedule_type = schedule_data.schedule_type
    if schedule_data.target_namespace is not None:
        schedule.target_namespace = schedule_data.target_namespace

    if schedule_data.target_name is not None:
        # Verify new target exists
        effective_type = schedule_data.schedule_type or schedule.schedule_type
        effective_ns = schedule_data.target_namespace or schedule.target_namespace

        if effective_type == "function":
            from app.models.function import Function

            target = await Function.get_by_name(
                db, effective_ns, schedule_data.target_name, user_id
            )
            if not target:
                raise HTTPException(
                    status_code=404,
                    detail=f"Function '{effective_ns}/{schedule_data.target_name}' not found",
                )
        else:
            from app.models.agent import Agent

            result = await db.execute(
                select(Agent).where(
                    and_(
                        Agent.namespace == effective_ns,
                        Agent.name == schedule_data.target_name,
                    )
                )
            )
            if not result.scalar_one_or_none():
                raise HTTPException(
                    status_code=404,
                    detail=f"Agent '{effective_ns}/{schedule_data.target_name}' not found",
                )
        schedule.target_name = schedule_data.target_name

    if schedule_data.description is not None:
        schedule.description = schedule_data.description
    if schedule_data.cron_expression is not None:
        schedule.cron_expression = schedule_data.cron_expression
    if schedule_data.timezone is not None:
        schedule.timezone = schedule_data.timezone
    if schedule_data.input_data is not None:
        schedule.input_data = schedule_data.input_data
    if schedule_data.content is not None:
        schedule.content = schedule_data.content
    if schedule_data.is_active is not None:
        schedule.is_active = schedule_data.is_active
    await db.commit()
    await db.refresh(schedule)

    await _notify_scheduler("update", str(schedule.id))

    response = ScheduledJobResponse.model_validate(schedule)

    return response


@router.delete("/{name}")
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

    await _notify_scheduler("remove", str(schedule.id))

    await db.delete(schedule)
    await db.commit()

    return {"message": f"Schedule '{schedule.name}' deleted successfully"}
