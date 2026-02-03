"""Schedules API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models.schedule import ScheduledJob
from app.schemas import ScheduledJobCreate, ScheduledJobResponse, ScheduledJobUpdate

router = APIRouter(prefix="/schedules", tags=["schedules"])


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

    # Verify function exists
    from app.models.function import Function

    function = await Function.get_by_name(
        db, schedule_data.function_namespace, schedule_data.function_name, user_id
    )
    if not function:
        raise HTTPException(
            status_code=404,
            detail=f"Function '{schedule_data.function_namespace}/{schedule_data.function_name}' not found",
        )
    # Create schedule
    schedule = ScheduledJob(
        user_id=user_id,
        name=schedule_data.name,
        function_namespace=schedule_data.function_namespace,
        function_name=schedule_data.function_name,
        description=schedule_data.description,
        cron_expression=schedule_data.cron_expression,
        timezone=schedule_data.timezone,
        input_data=schedule_data.input_data,
    )

    db.add(schedule)
    await db.commit()
    await db.refresh(schedule)

    # TODO: Register job with scheduler

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
    if schedule_data.function_name is not None:
        # Verify new function exists
        from app.models.function import Function

        result = await db.execute(
            select(Function).where(
                and_(Function.user_id == user_id, Function.name == schedule_data.function_name)
            )
        )
        if not result.scalar_one_or_none():
            raise HTTPException(
                status_code=404, detail=f"Function '{schedule_data.function_name}' not found"
            )
        schedule.function_name = schedule_data.function_name

    if schedule_data.description is not None:
        schedule.description = schedule_data.description
    if schedule_data.cron_expression is not None:
        schedule.cron_expression = schedule_data.cron_expression
    if schedule_data.timezone is not None:
        schedule.timezone = schedule_data.timezone
    if schedule_data.input_data is not None:
        schedule.input_data = schedule_data.input_data
    if schedule_data.is_active is not None:
        schedule.is_active = schedule_data.is_active
    await db.commit()
    await db.refresh(schedule)

    # TODO: Update job in scheduler

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

    # TODO: Remove job from scheduler

    await db.delete(schedule)
    await db.commit()

    return {"message": f"Schedule '{schedule.name}' deleted successfully"}
