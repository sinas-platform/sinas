"""Standalone scheduler service for singleton tasks.

Runs as a separate process (python -m app.scheduler.service) so the backend
can be a pure stateless API server.  Handles:
  - Declarative config apply (on startup)
  - Sandbox container initialization
  - Shared container management
  - APScheduler cron jobs
"""

import asyncio
import json
import logging
import signal
import uuid

import asyncpg
from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.encryption import encryption_service
from app.core.redis import close_redis, get_redis
from app.models.database_connection import DatabaseConnection
from app.models.schedule import ScheduledJob
from app.models.user import Role, User, UserRole
from app.scheduler.jobs.cleanup_expired_chats import cleanup_expired_chats
from app.services.config_apply import ConfigApplyService
from app.services.config_parser import ConfigParser
from app.services.container_pool import container_pool
from app.services.scheduler import scheduler
from app.services.shared_worker_manager import shared_worker_manager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [scheduler] %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

SCHEDULER_CHANNEL = "sinas:scheduler:jobs"


async def _maintain_tool_result_partitions() -> None:
    """Create future partitions and drop expired ones for tool_call_results."""
    from app.services.tool_result_store import ensure_partitions, cleanup_expired_partitions
    async with AsyncSessionLocal() as db:
        await ensure_partitions(db)
        dropped = await cleanup_expired_partitions(db)
        if dropped:
            logger.info(f"Dropped {dropped} expired tool_call_results partition(s)")


async def _initialize_builtin_database() -> None:
    """Ensure the sinas_data database and its Database Connection record exist."""
    direct_host = settings.database_direct_host or settings.database_host
    try:
        conn = await asyncpg.connect(
            host=direct_host,
            port=int(settings.database_port),
            user=settings.database_user,
            password=settings.database_password,
            database=settings.database_name,
        )
        try:
            exists = await conn.fetchval(
                "SELECT 1 FROM pg_database WHERE datname = 'sinas_data'"
            )
            if not exists:
                await conn.execute("CREATE DATABASE sinas_data")
                print("✅ Created sinas_data database")
        finally:
            await conn.close()
    except Exception as e:
        print(f"⚠️  Could not ensure sinas_data database: {e}")
        logger.warning(f"Could not ensure sinas_data database: {e}")
        return

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(DatabaseConnection).where(DatabaseConnection.name == "built-in")
        )
        existing = result.scalar_one_or_none()

        if existing:
            # Ensure host points at postgres directly (not pgbouncer)
            if existing.host != direct_host:
                existing.host = direct_host
                await db.commit()
                print(f"✅ Updated built-in database connection host to {direct_host}")
            return

        connection = DatabaseConnection(
            name="built-in",
            connection_type="postgresql",
            host=direct_host,
            port=int(settings.database_port),
            database="sinas_data",
            username=settings.database_user,
            password=encryption_service.encrypt(settings.database_password),
            is_active=True,
            read_only=False,
            managed_by="system",
            config={"pool_size": 5, "max_overflow": 10},
        )
        db.add(connection)
        await db.commit()
        print("✅ Built-in database connection created (sinas_data)")


async def _listen_for_job_changes(stop_event: asyncio.Event) -> None:
    """Subscribe to Redis pub/sub and apply job changes to APScheduler."""
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
    # --- Redis ---
    redis = await get_redis()
    await redis.ping()
    print("✅ Redis connection established")

    # --- Declarative config apply ---
    if settings.config_file and settings.auto_apply_config:
        logger.info(f"🔧 AUTO_APPLY_CONFIG enabled, applying config from {settings.config_file}...")
        async with AsyncSessionLocal() as db:
            try:
                # Look up superadmin user to own config-created resources
                admin_role_result = await db.execute(
                    select(Role).where(Role.name == "Admins")
                )
                admin_role = admin_role_result.scalar_one_or_none()
                owner_user_id = None

                if admin_role:
                    admin_member_result = await db.execute(
                        select(UserRole).where(UserRole.role_id == admin_role.id).limit(1)
                    )
                    admin_member = admin_member_result.scalar_one_or_none()
                    if admin_member:
                        owner_user_id = str(admin_member.user_id)

                if not owner_user_id:
                    # Fallback: use any user
                    any_user_result = await db.execute(select(User).limit(1))
                    any_user = any_user_result.scalar_one_or_none()
                    if any_user:
                        owner_user_id = str(any_user.id)

                if not owner_user_id:
                    raise RuntimeError("No users found in database — cannot apply config")

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

                apply_service = ConfigApplyService(
                    db, config.metadata.name, owner_user_id=owner_user_id
                )
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

    # --- Built-in database (sinas_data) ---
    await _initialize_builtin_database()

    # --- Sandbox containers ---
    async with AsyncSessionLocal() as db:
        await container_pool.initialize(db)

    # --- Shared containers ---
    await shared_worker_manager.initialize()

    # --- APScheduler ---
    await scheduler.start()

    # --- System jobs ---
    scheduler.scheduler.add_job(
        func=cleanup_expired_chats,
        trigger="interval",
        hours=1,
        id="system:cleanup_expired_chats",
        name="Cleanup expired chats",
        replace_existing=True,
    )
    logger.info("Registered system job: cleanup_expired_chats (every 1h)")

    # Ensure tool_call_results partitions exist
    await _maintain_tool_result_partitions()

    scheduler.scheduler.add_job(
        func=_maintain_tool_result_partitions,
        trigger="interval",
        hours=24,
        id="system:maintain_tool_result_partitions",
        name="Maintain tool_call_results partitions",
        replace_existing=True,
    )
    logger.info("Registered system job: maintain_tool_result_partitions (every 24h)")

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
