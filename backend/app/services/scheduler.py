import logging
import uuid
from datetime import datetime
from typing import Any

from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.execution import TriggerType
from app.models.schedule import ScheduledJob

logger = logging.getLogger(__name__)


async def _execute_scheduled_function(
    job_id: str,
    target_namespace: str,
    target_name: str,
    input_data: dict[str, Any],
    user_id: str,
):
    """Enqueue a scheduled function for execution via the job queue."""
    from app.services.queue_service import queue_service

    execution_id = str(uuid.uuid4())

    try:
        logger.info(
            f"Enqueuing scheduled function: {target_namespace}/{target_name} (job: {job_id})"
        )

        # Update last_run time in database
        await _update_job_last_run(job_id)

        # Enqueue the function for async execution
        queue_job_id = await queue_service.enqueue_function(
            function_namespace=target_namespace,
            function_name=target_name,
            input_data=input_data,
            execution_id=execution_id,
            trigger_type=TriggerType.SCHEDULE.value,
            trigger_id=job_id,
            user_id=user_id,
        )

        logger.info(
            f"Scheduled function {target_namespace}/{target_name} enqueued: "
            f"job_id={queue_job_id}"
        )

    except Exception as e:
        logger.error(f"Scheduled function {target_namespace}/{target_name} enqueue failed: {e}")


async def _execute_scheduled_agent(
    job_id: str,
    target_namespace: str,
    target_name: str,
    content: str,
    input_data: dict[str, Any],
    user_id: str,
    schedule_name: str,
):
    """Create a fresh chat and enqueue an agent message for a scheduled agent run."""
    from app.core.auth import create_access_token
    from app.models.agent import Agent
    from app.models.chat import Chat
    from app.models.user import User
    from app.services.queue_service import queue_service

    try:
        logger.info(
            f"Enqueuing scheduled agent: {target_namespace}/{target_name} (job: {job_id})"
        )

        await _update_job_last_run(job_id)

        async with AsyncSessionLocal() as db:
            # Look up user for email (needed for JWT)
            result = await db.execute(
                select(User).where(User.id == uuid.UUID(user_id))
            )
            user = result.scalar_one_or_none()
            if not user:
                logger.error(f"User {user_id} not found for scheduled agent job {job_id}")
                return

            # Look up agent
            result = await db.execute(
                select(Agent).where(
                    Agent.namespace == target_namespace,
                    Agent.name == target_name,
                )
            )
            agent = result.scalar_one_or_none()
            if not agent:
                logger.error(
                    f"Agent {target_namespace}/{target_name} not found for scheduled job {job_id}"
                )
                return

            # Mint a short-lived JWT for the queue worker
            token = create_access_token(user_id=str(user.id), email=user.email)

            # Create a fresh chat for this run
            timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
            chat = Chat(
                user_id=user.id,
                agent_id=agent.id,
                agent_namespace=agent.namespace,
                agent_name=agent.name,
                title=f"{schedule_name} — {timestamp}",
                chat_metadata={"agent_input": input_data} if input_data else None,
            )
            db.add(chat)
            await db.commit()
            await db.refresh(chat)

            chat_id = str(chat.id)

        # Enqueue the agent message
        channel_id = str(uuid.uuid4())
        await queue_service.enqueue_agent_message(
            chat_id=chat_id,
            user_id=user_id,
            user_token=token,
            content=content,
            channel_id=channel_id,
            agent=f"{target_namespace}/{target_name}",
            trigger_type="schedule",
        )

        logger.info(
            f"Scheduled agent {target_namespace}/{target_name} enqueued: "
            f"chat_id={chat_id}"
        )

    except Exception as e:
        logger.error(
            f"Scheduled agent {target_namespace}/{target_name} enqueue failed: {e}"
        )


async def _update_job_last_run(job_id: str):
    """Update the last_run and next_run timestamps for a scheduled job."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ScheduledJob).where(ScheduledJob.id == uuid.UUID(job_id))
        )
        job = result.scalar_one_or_none()

        if job:
            job.last_run = datetime.utcnow()

            # Calculate next run time from cron expression
            from croniter import croniter

            cron = croniter(job.cron_expression, datetime.utcnow())
            job.next_run = cron.get_next(datetime)

            await db.commit()


class FunctionScheduler:
    def __init__(self):
        # Configure job stores and executors
        jobstores = {"default": SQLAlchemyJobStore(url=settings.get_database_url)}
        executors = {"default": AsyncIOExecutor()}

        job_defaults = {"coalesce": False, "max_instances": 3}

        self.scheduler = AsyncIOScheduler(
            jobstores=jobstores, executors=executors, job_defaults=job_defaults
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
            result = await db.execute(select(ScheduledJob).where(ScheduledJob.is_active == True))
            jobs = result.scalars().all()

            for job in jobs:
                await self._add_job_to_scheduler(job)
                logger.info(f"Loaded scheduled job: {job.name}")

    async def _add_job_to_scheduler(self, job: ScheduledJob):
        """Add a single job to the APScheduler."""
        try:
            cron_params = self._parse_cron_expression(job.cron_expression)

            if job.schedule_type == "agent":
                self.scheduler.add_job(
                    func=_execute_scheduled_agent,
                    trigger="cron",
                    args=[
                        str(job.id),
                        job.target_namespace,
                        job.target_name,
                        job.content or "",
                        job.input_data,
                        str(job.user_id),
                        job.name,
                    ],
                    id=str(job.id),
                    name=job.name,
                    timezone=job.timezone,
                    **cron_params,
                    replace_existing=True,
                )
            else:
                self.scheduler.add_job(
                    func=_execute_scheduled_function,
                    trigger="cron",
                    args=[
                        str(job.id),
                        job.target_namespace,
                        job.target_name,
                        job.input_data,
                        str(job.user_id),
                    ],
                    id=str(job.id),
                    name=job.name,
                    timezone=job.timezone,
                    **cron_params,
                    replace_existing=True,
                )
        except Exception as e:
            logger.error(f"Failed to add job {job.name} to scheduler: {e}")

    def _parse_cron_expression(self, cron_expr: str) -> dict[str, Any]:
        """Parse cron expression into APScheduler cron trigger parameters."""
        parts = cron_expr.split()

        if len(parts) != 5:
            raise ValueError("Cron expression must have 5 parts: minute hour day month day_of_week")

        minute, hour, day, month, day_of_week = parts

        return {
            "minute": minute,
            "hour": hour,
            "day": day,
            "month": month,
            "day_of_week": day_of_week,
        }

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

    def get_scheduler_status(self) -> dict[str, Any]:
        """Get current scheduler status and job information."""
        if not self._started:
            return {"status": "stopped", "jobs": []}

        jobs = []
        for job in self.scheduler.get_jobs():
            jobs.append(
                {
                    "id": job.id,
                    "name": job.name,
                    "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
                    "trigger": str(job.trigger),
                }
            )

        return {"status": "running", "jobs": jobs, "total_jobs": len(jobs)}


# Global scheduler instance
scheduler = FunctionScheduler()
