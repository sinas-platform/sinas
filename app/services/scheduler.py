import asyncio
import uuid
from datetime import datetime
from typing import Dict, Any, Optional
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.asyncio import AsyncIOExecutor
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
import logging

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.schedule import ScheduledJob
from app.models.execution import TriggerType
from app.services.execution_engine import executor

logger = logging.getLogger(__name__)


class FunctionScheduler:
    def __init__(self):
        # Configure job stores and executors
        jobstores = {
            'default': SQLAlchemyJobStore(url=settings.database_url)
        }
        executors = {
            'default': AsyncIOExecutor()
        }
        
        job_defaults = {
            'coalesce': False,
            'max_instances': 3
        }
        
        self.scheduler = AsyncIOScheduler(
            jobstores=jobstores,
            executors=executors,
            job_defaults=job_defaults
        )
        
        self._started = False
    
    async def start(self):
        """Start the scheduler and load existing jobs."""
        if self._started:
            return
        
        self.scheduler.start()
        self._started = True
        
        # Load existing active scheduled jobs from database
        await self._load_scheduled_jobs()
        
        logger.info("Function scheduler started")
    
    async def stop(self):
        """Stop the scheduler."""
        if not self._started:
            return
        
        self.scheduler.shutdown()
        self._started = False
        
        logger.info("Function scheduler stopped")
    
    async def _load_scheduled_jobs(self):
        """Load all active scheduled jobs from database and add to scheduler."""
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(ScheduledJob).where(ScheduledJob.is_active == True)
            )
            jobs = result.scalars().all()
            
            for job in jobs:
                await self._add_job_to_scheduler(job)
                logger.info(f"Loaded scheduled job: {job.name}")
    
    async def _add_job_to_scheduler(self, job: ScheduledJob):
        """Add a single job to the APScheduler."""
        try:
            self.scheduler.add_job(
                func=self._execute_scheduled_function,
                trigger='cron',
                args=[str(job.id), job.function_name, job.input_data],
                id=str(job.id),
                name=job.name,
                timezone=job.timezone,
                **self._parse_cron_expression(job.cron_expression),
                replace_existing=True
            )
        except Exception as e:
            logger.error(f"Failed to add job {job.name} to scheduler: {e}")
    
    def _parse_cron_expression(self, cron_expr: str) -> Dict[str, Any]:
        """Parse cron expression into APScheduler cron trigger parameters."""
        parts = cron_expr.split()
        
        if len(parts) != 5:
            raise ValueError("Cron expression must have 5 parts: minute hour day month day_of_week")
        
        minute, hour, day, month, day_of_week = parts
        
        return {
            'minute': minute,
            'hour': hour,
            'day': day,
            'month': month,
            'day_of_week': day_of_week
        }
    
    async def _execute_scheduled_function(
        self, 
        job_id: str, 
        function_name: str, 
        input_data: Dict[str, Any]
    ):
        """Execute a scheduled function."""
        execution_id = str(uuid.uuid4())
        
        try:
            logger.info(f"Executing scheduled function: {function_name} (job: {job_id})")
            
            # Update last_run time in database
            await self._update_job_last_run(job_id)
            
            # Execute the function
            result = await executor.execute_function(
                function_name=function_name,
                input_data=input_data,
                execution_id=execution_id,
                trigger_type=TriggerType.SCHEDULE.value,
                trigger_id=job_id
            )
            
            logger.info(f"Scheduled function {function_name} completed successfully")
            
        except Exception as e:
            logger.error(f"Scheduled function {function_name} failed: {e}")
    
    async def _update_job_last_run(self, job_id: str):
        """Update the last_run timestamp for a scheduled job."""
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(ScheduledJob).where(ScheduledJob.id == uuid.UUID(job_id))
            )
            job = result.scalar_one_or_none()
            
            if job:
                job.last_run = datetime.utcnow()
                await db.commit()
    
    async def add_job(self, job: ScheduledJob):
        """Add a new scheduled job."""
        if self._started:
            await self._add_job_to_scheduler(job)
    
    async def update_job(self, job: ScheduledJob):
        """Update an existing scheduled job."""
        if self._started:
            # Remove existing job and add updated version
            try:
                self.scheduler.remove_job(str(job.id))
            except Exception:
                pass  # Job might not exist in scheduler
            
            if job.is_active:
                await self._add_job_to_scheduler(job)
    
    async def remove_job(self, job_id: str):
        """Remove a scheduled job from the scheduler."""
        if self._started:
            try:
                self.scheduler.remove_job(job_id)
            except Exception as e:
                logger.warning(f"Failed to remove job {job_id} from scheduler: {e}")
    
    def get_scheduler_status(self) -> Dict[str, Any]:
        """Get current scheduler status and job information."""
        if not self._started:
            return {"status": "stopped", "jobs": []}
        
        jobs = []
        for job in self.scheduler.get_jobs():
            jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
                "trigger": str(job.trigger)
            })
        
        return {
            "status": "running",
            "jobs": jobs,
            "total_jobs": len(jobs)
        }


# Global scheduler instance
scheduler = FunctionScheduler()