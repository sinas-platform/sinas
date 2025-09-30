from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
import uuid
from datetime import datetime
from croniter import croniter

from app.core.database import get_db
from app.models.schedule import ScheduledJob
from app.api.schemas import ScheduledJobCreate, ScheduledJobUpdate, ScheduledJobResponse
from app.services.scheduler import scheduler

router = APIRouter(prefix="/schedules", tags=["schedules"])


@router.post("", response_model=ScheduledJobResponse)
async def create_scheduled_job(
    job_data: ScheduledJobCreate,
    db: AsyncSession = Depends(get_db)
):
    # Check if job name already exists
    existing = await db.execute(
        select(ScheduledJob).where(ScheduledJob.name == job_data.name)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Scheduled job with this name already exists")
    
    # Calculate next run time
    cron = croniter(job_data.cron_expression, datetime.utcnow())
    next_run = cron.get_next(datetime)
    
    # Create scheduled job
    job = ScheduledJob(
        name=job_data.name,
        function_name=job_data.function_name,
        description=job_data.description,
        cron_expression=job_data.cron_expression,
        timezone=job_data.timezone,
        input_data=job_data.input_data,
        next_run=next_run
    )
    
    db.add(job)
    await db.commit()
    await db.refresh(job)
    
    # Add to scheduler
    await scheduler.add_job(job)
    
    return job


@router.get("", response_model=List[ScheduledJobResponse])
async def list_scheduled_jobs(
    active_only: bool = True,
    db: AsyncSession = Depends(get_db)
):
    query = select(ScheduledJob)
    
    if active_only:
        query = query.where(ScheduledJob.is_active == True)
    
    result = await db.execute(query.order_by(ScheduledJob.name))
    jobs = result.scalars().all()
    
    return jobs


@router.get("/{job_id}", response_model=ScheduledJobResponse)
async def get_scheduled_job(job_id: str, db: AsyncSession = Depends(get_db)):
    try:
        job_uuid = uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job ID format")
    
    result = await db.execute(
        select(ScheduledJob).where(ScheduledJob.id == job_uuid)
    )
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Scheduled job not found")
    
    return job


@router.put("/{job_id}", response_model=ScheduledJobResponse)
async def update_scheduled_job(
    job_id: str,
    job_data: ScheduledJobUpdate,
    db: AsyncSession = Depends(get_db)
):
    try:
        job_uuid = uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job ID format")
    
    result = await db.execute(
        select(ScheduledJob).where(ScheduledJob.id == job_uuid)
    )
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Scheduled job not found")
    
    # Update job fields
    update_data = job_data.dict(exclude_unset=True)
    
    # Recalculate next run if cron expression changed
    if "cron_expression" in update_data:
        cron = croniter(update_data["cron_expression"], datetime.utcnow())
        update_data["next_run"] = cron.get_next(datetime)
    
    for field, value in update_data.items():
        setattr(job, field, value)
    
    await db.commit()
    await db.refresh(job)
    
    # Update scheduler
    await scheduler.update_job(job)
    
    return job


@router.delete("/{job_id}")
async def delete_scheduled_job(job_id: str, db: AsyncSession = Depends(get_db)):
    try:
        job_uuid = uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job ID format")
    
    result = await db.execute(
        select(ScheduledJob).where(ScheduledJob.id == job_uuid)
    )
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Scheduled job not found")
    
    # Soft delete by setting is_active to False
    job.is_active = False
    
    await db.commit()
    
    # Remove from scheduler
    await scheduler.remove_job(str(job.id))
    
    return {"message": "Scheduled job deleted successfully"}