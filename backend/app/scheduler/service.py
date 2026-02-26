"""Standalone scheduler service for singleton tasks.

Runs as a separate process (python -m app.scheduler.service) so the backend
can be a pure stateless API server.  Handles:
  - Declarative config apply (on startup)
  - Container pool initialization
  - Shared worker management
  - APScheduler cron jobs
"""

import asyncio
import json
import logging
import signal
import uuid

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [scheduler] %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

SCHEDULER_CHANNEL = "sinas:scheduler:jobs"


async def _listen_for_job_changes(stop_event: asyncio.Event) -> None:
    """Subscribe to Redis pub/sub and apply job changes to APScheduler."""
    from app.core.database import AsyncSessionLocal
    from app.core.redis import get_redis
    from app.models.schedule import ScheduledJob
    from app.services.scheduler import scheduler

    redis = await get_redis()
    pubsub = redis.pubsub()
    await pubsub.subscribe(SCHEDULER_CHANNEL)
    logger.info(f"Listening for job changes on {SCHEDULER_CHANNEL}")

    try:
        while not stop_event.is_set():
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if msg is None:
                continue

            try:
                payload = json.loads(msg["data"])
                action = payload["action"]
                job_id = payload["job_id"]
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Invalid scheduler message: {e}")
                continue

            if action == "remove":
                await scheduler.remove_job(job_id)
                logger.info(f"Removed job {job_id} from scheduler")
            elif action in ("add", "update"):
                async with AsyncSessionLocal() as db:
                    from sqlalchemy import select

                    result = await db.execute(
                        select(ScheduledJob).where(ScheduledJob.id == uuid.UUID(job_id))
                    )
                    job = result.scalar_one_or_none()

                if job is None:
                    logger.warning(f"Job {job_id} not found in DB, skipping {action}")
                    continue

                if action == "add":
                    await scheduler.add_job(job)
                    logger.info(f"Added job {job_id} ({job.name}) to scheduler")
                else:
                    await scheduler.update_job(job)
                    logger.info(f"Updated job {job_id} ({job.name}) in scheduler")
            else:
                logger.warning(f"Unknown scheduler action: {action}")
    finally:
        await pubsub.unsubscribe(SCHEDULER_CHANNEL)
        await pubsub.aclose()


async def main() -> None:
    from app.core.config import settings
    from app.core.database import AsyncSessionLocal
    from app.core.redis import close_redis, get_redis
    from app.services.container_pool import container_pool
    from app.services.scheduler import scheduler
    from app.services.shared_worker_manager import shared_worker_manager

    # --- Redis ---
    redis = await get_redis()
    await redis.ping()
    print("✅ Redis connection established")

    # --- Declarative config apply ---
    if settings.config_file and settings.auto_apply_config:
        logger.info(f"🔧 AUTO_APPLY_CONFIG enabled, applying config from {settings.config_file}...")
        async with AsyncSessionLocal() as db:
            from app.services.config_apply import ConfigApplyService
            from app.services.config_parser import ConfigParser

            try:
                with open(settings.config_file) as f:
                    config_yaml = f.read()

                config, validation = await ConfigParser.parse_and_validate(
                    config_yaml, db=db, strict=False
                )

                if not validation.valid:
                    logger.error("❌ Config validation failed:")
                    for error in validation.errors:
                        logger.error(f"  - {error.path}: {error.message}")
                    raise RuntimeError("Config validation failed")

                if validation.warnings:
                    logger.warning("⚠️  Config validation warnings:")
                    for warning in validation.warnings:
                        logger.warning(f"  - {warning.path}: {warning.message}")

                apply_service = ConfigApplyService(db, config.metadata.name)
                result = await apply_service.apply_config(config, dry_run=False)

                if not result.success:
                    logger.error("❌ Config application failed:")
                    for error in result.errors:
                        logger.error(f"  - {error}")
                    raise RuntimeError("Config application failed")

                logger.info("✅ Config applied successfully!")
                if result.summary.created:
                    logger.info(f"  Created: {dict(result.summary.created)}")
                if result.summary.updated:
                    logger.info(f"  Updated: {dict(result.summary.updated)}")
                if result.summary.unchanged:
                    logger.info(f"  Unchanged: {dict(result.summary.unchanged)}")

            except FileNotFoundError:
                logger.error(f"❌ Config file not found: {settings.config_file}")
                raise
            except Exception as e:
                logger.error(f"❌ Failed to apply config: {e}", exc_info=True)
                raise

    # --- Container pool ---
    async with AsyncSessionLocal() as db:
        await container_pool.initialize(db)

    # --- Shared workers ---
    await shared_worker_manager.initialize()

    # --- APScheduler ---
    await scheduler.start()

    # --- System jobs ---
    from app.scheduler.jobs.cleanup_expired_chats import cleanup_expired_chats

    scheduler.scheduler.add_job(
        func=cleanup_expired_chats,
        trigger="interval",
        hours=1,
        id="system:cleanup_expired_chats",
        name="Cleanup expired chats",
        replace_existing=True,
    )
    logger.info("Registered system job: cleanup_expired_chats (every 1h)")

    # --- Pub/sub listener for live job changes ---
    stop_event = asyncio.Event()
    listener_task = asyncio.create_task(_listen_for_job_changes(stop_event))

    print("🚀 Scheduler service running — press Ctrl+C or send SIGTERM to stop")

    # Block until shutdown signal
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)
    await stop_event.wait()

    # --- Graceful shutdown ---
    print("🛑 Shutting down scheduler service...")
    listener_task.cancel()
    try:
        await listener_task
    except asyncio.CancelledError:
        pass
    await scheduler.stop()
    await container_pool.shutdown()
    await close_redis()
    print("👋 Scheduler service stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
