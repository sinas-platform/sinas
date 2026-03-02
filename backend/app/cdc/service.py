"""CDC (Change Data Capture) polling service.

Runs as a separate process (python -m app.cdc.service).
Polls external databases for changes and enqueues function executions.
"""

import asyncio
import json
import logging
import signal
import uuid
from datetime import datetime, timezone
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [cdc] %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

CDC_CHANNEL = "sinas:cdc:triggers"


def _serialize_value(val: Any) -> Any:
    """Convert non-JSON-serializable types to JSON-safe values.

    Reuses the same logic as DatabasePoolManager._serialize_value().
    """
    import datetime as dt
    from decimal import Decimal

    if val is None:
        return None
    if isinstance(val, (uuid.UUID, Decimal)):
        return str(val)
    if isinstance(val, (dt.datetime, dt.date, dt.time)):
        return val.isoformat()
    if isinstance(val, dt.timedelta):
        return val.total_seconds()
    if isinstance(val, bytes):
        return val.hex()
    if isinstance(val, (list, tuple)):
        return [_serialize_value(v) for v in val]
    if isinstance(val, dict):
        return {k: _serialize_value(v) for k, v in val.items()}
    return val


def _serialize_row(row) -> dict[str, Any]:
    """Convert an asyncpg Record to a JSON-safe dict."""
    return {key: _serialize_value(row[key]) for key in row.keys()}


class CDCManager:
    """Manages poll loops for all active CDC triggers."""

    def __init__(self):
        self._poll_tasks: dict[str, asyncio.Task] = {}  # trigger_id -> task
        self._stop_event = asyncio.Event()

    async def start(self):
        """Load all active triggers from DB and start poll loops."""
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        from app.core.database import AsyncSessionLocal
        from app.models.database_trigger import DatabaseTrigger

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(DatabaseTrigger).where(DatabaseTrigger.is_active == True)
            )
            triggers = result.scalars().all()

        logger.info(f"Loaded {len(triggers)} active CDC triggers")
        for trigger in triggers:
            self._start_poll_task(str(trigger.id))

    async def stop(self):
        """Cancel all poll tasks."""
        self._stop_event.set()
        for trigger_id, task in self._poll_tasks.items():
            task.cancel()
        for trigger_id, task in self._poll_tasks.items():
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._poll_tasks.clear()
        logger.info("All CDC poll tasks stopped")

    async def handle_trigger_change(self, action: str, trigger_id: str):
        """Handle pub/sub notification: add/update/remove a trigger's poll task."""
        if action == "remove":
            if trigger_id in self._poll_tasks:
                self._poll_tasks[trigger_id].cancel()
                try:
                    await self._poll_tasks[trigger_id]
                except asyncio.CancelledError:
                    pass
                del self._poll_tasks[trigger_id]
                logger.info(f"Removed CDC poll task for trigger {trigger_id}")
        elif action in ("add", "update"):
            # Cancel existing task if any
            if trigger_id in self._poll_tasks:
                self._poll_tasks[trigger_id].cancel()
                try:
                    await self._poll_tasks[trigger_id]
                except asyncio.CancelledError:
                    pass
            self._start_poll_task(trigger_id)
            logger.info(f"{'Added' if action == 'add' else 'Updated'} CDC poll task for trigger {trigger_id}")
        else:
            logger.warning(f"Unknown CDC action: {action}")

    def _start_poll_task(self, trigger_id: str):
        """Start a background poll loop task for one trigger."""
        task = asyncio.create_task(self._run_poll_loop(trigger_id))
        self._poll_tasks[trigger_id] = task

    async def _run_poll_loop(self, trigger_id: str):
        """Main poll loop for one trigger. Retries with exponential backoff on errors."""
        from sqlalchemy import select, update

        from app.core.database import AsyncSessionLocal
        from app.models.database_trigger import DatabaseTrigger
        from app.services.database_pool import DatabasePoolManager
        from app.services.queue_service import queue_service

        pool_manager = DatabasePoolManager.get_instance()
        backoff = 1

        while not self._stop_event.is_set():
            try:
                # Load trigger from DB (fresh each iteration to pick up changes)
                async with AsyncSessionLocal() as db:
                    result = await db.execute(
                        select(DatabaseTrigger).where(DatabaseTrigger.id == uuid.UUID(trigger_id))
                    )
                    trigger = result.scalar_one_or_none()

                if trigger is None:
                    logger.warning(f"Trigger {trigger_id} not found in DB, stopping poll loop")
                    break

                if not trigger.is_active:
                    logger.info(f"Trigger {trigger_id} ({trigger.name}) is inactive, stopping poll loop")
                    break

                conn_id = str(trigger.database_connection_id)
                schema = trigger.schema_name
                table = trigger.table_name
                poll_col = trigger.poll_column
                batch_size = trigger.batch_size
                last_value = trigger.last_poll_value
                interval = trigger.poll_interval_seconds

                # Get pool for external database
                async with AsyncSessionLocal() as db:
                    pool = await pool_manager.get_pool(db, conn_id)

                async with pool.acquire() as conn:
                    # Look up the column's data type so we can cast the text
                    # bookmark back to the native type for correct ordering.
                    col_type_row = await conn.fetchrow(
                        "SELECT data_type FROM information_schema.columns "
                        "WHERE table_schema = $1 AND table_name = $2 AND column_name = $3",
                        schema,
                        table,
                        poll_col,
                    )
                    if not col_type_row:
                        raise ValueError(
                            f"Column '{poll_col}' not found in {schema}.{table}"
                        )
                    col_type = col_type_row["data_type"]

                    if last_value is None:
                        # First poll: set bookmark to current max without triggering
                        max_row = await conn.fetchrow(
                            f'SELECT MAX("{poll_col}")::text as max_val '
                            f'FROM "{schema}"."{table}"'
                        )
                        if max_row and max_row["max_val"] is not None:
                            new_bookmark = max_row["max_val"]
                        else:
                            new_bookmark = None

                        async with AsyncSessionLocal() as db:
                            await db.execute(
                                update(DatabaseTrigger)
                                .where(DatabaseTrigger.id == uuid.UUID(trigger_id))
                                .values(
                                    last_poll_value=new_bookmark,
                                    error_message=None,
                                )
                            )
                            await db.commit()

                        logger.info(
                            f"Trigger {trigger.name}: initialized bookmark to {new_bookmark}"
                        )
                    else:
                        # Regular poll: cast text bookmark to the column's native
                        # type so comparison and ordering work correctly.
                        rows = await conn.fetch(
                            f'SELECT * FROM "{schema}"."{table}" '
                            f"WHERE \"{poll_col}\" > $1::{col_type} "
                            f'ORDER BY "{poll_col}" ASC '
                            f"LIMIT $2",
                            last_value,
                            batch_size,
                        )

                        if rows:
                            serialized_rows = [_serialize_row(row) for row in rows]
                            new_bookmark = _serialize_value(rows[-1][poll_col])
                            if not isinstance(new_bookmark, str):
                                new_bookmark = str(new_bookmark)

                            payload = {
                                "table": f"{schema}.{table}",
                                "operation": "CHANGE",
                                "rows": serialized_rows,
                                "poll_column": poll_col,
                                "count": len(serialized_rows),
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            }

                            # Enqueue function execution
                            execution_id = str(uuid.uuid4())
                            await queue_service.enqueue_function(
                                function_namespace=trigger.function_namespace,
                                function_name=trigger.function_name,
                                input_data=payload,
                                execution_id=execution_id,
                                trigger_type="CDC",
                                trigger_id=trigger_id,
                                user_id=str(trigger.user_id),
                            )

                            # Update bookmark and clear error
                            async with AsyncSessionLocal() as db:
                                await db.execute(
                                    update(DatabaseTrigger)
                                    .where(DatabaseTrigger.id == uuid.UUID(trigger_id))
                                    .values(
                                        last_poll_value=new_bookmark,
                                        error_message=None,
                                    )
                                )
                                await db.commit()

                            logger.info(
                                f"Trigger {trigger.name}: found {len(rows)} rows, "
                                f"enqueued execution {execution_id[:8]}, "
                                f"bookmark -> {new_bookmark}"
                            )

                # Reset backoff on success
                backoff = 1

                # Sleep for poll interval
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
                    break  # stop_event was set
                except asyncio.TimeoutError:
                    pass  # Normal: timeout means we should poll again

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Trigger {trigger_id}: poll error: {e}", exc_info=True)

                # Update error_message in DB
                try:
                    async with AsyncSessionLocal() as db:
                        await db.execute(
                            update(DatabaseTrigger)
                            .where(DatabaseTrigger.id == uuid.UUID(trigger_id))
                            .values(error_message=str(e))
                        )
                        await db.commit()
                except Exception:
                    pass

                # Exponential backoff (max 60s)
                sleep_time = min(backoff, 60)
                backoff = min(backoff * 2, 60)
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=sleep_time)
                    break
                except asyncio.TimeoutError:
                    pass


async def _listen_for_trigger_changes(manager: CDCManager, stop_event: asyncio.Event) -> None:
    """Subscribe to Redis pub/sub for trigger CRUD events."""
    from app.core.redis import get_redis

    redis = await get_redis()
    pubsub = redis.pubsub()
    await pubsub.subscribe(CDC_CHANNEL)
    logger.info(f"Listening for trigger changes on {CDC_CHANNEL}")

    try:
        while not stop_event.is_set():
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if msg is None:
                continue

            try:
                payload = json.loads(msg["data"])
                action = payload["action"]
                trigger_id = payload["trigger_id"]
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Invalid CDC message: {e}")
                continue

            await manager.handle_trigger_change(action, trigger_id)
    finally:
        await pubsub.unsubscribe(CDC_CHANNEL)
        await pubsub.aclose()


async def main() -> None:
    from app.core.redis import close_redis, get_redis

    # --- Redis ---
    redis = await get_redis()
    await redis.ping()
    print("✅ Redis connection established")

    # --- CDC Manager ---
    manager = CDCManager()
    await manager.start()

    # --- Pub/sub listener for live trigger changes ---
    stop_event = manager._stop_event
    listener_task = asyncio.create_task(_listen_for_trigger_changes(manager, stop_event))

    print("🚀 CDC service running — press Ctrl+C or send SIGTERM to stop")

    # Block until shutdown signal
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)
    await stop_event.wait()

    # --- Graceful shutdown ---
    print("🛑 Shutting down CDC service...")
    listener_task.cancel()
    try:
        await listener_task
    except asyncio.CancelledError:
        pass
    await manager.stop()
    await close_redis()
    print("👋 CDC service stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
