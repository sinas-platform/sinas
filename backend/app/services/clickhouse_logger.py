"""ClickHouse logger service for comprehensive request logging.

Nothing here runs ClickHouse I/O on the event loop. The client is
synchronous, and every request used to insert its log row inline, blocking
every other request and stream in the process while it ran. With ClickHouse
unreachable, each request also tried to reconnect, for up to the connect
timeout. Now log calls only buffer the row. A background task inserts the
buffer in batches from a thread, and a failed connection waits a while
before the next attempt. Logging stays best effort: rows are dropped when
ClickHouse is down or the buffer is full.
"""
import asyncio
import json
import logging
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Optional

import clickhouse_connect
from clickhouse_connect.driver.client import Client
from clickhouse_connect.driver.exceptions import OperationalError, StreamFailureError

from app.core.config import settings

logger = logging.getLogger(__name__)

FLUSH_INTERVAL_S = 1.0
MAX_BUFFERED_ROWS = 10_000  # per table
RETRY_AFTER_S = 30.0  # after a failed connection or insert
CONNECT_TIMEOUT_S = 3

REQUEST_COLUMNS = [
    "request_id",
    "timestamp",
    "user_id",
    "user_email",
    "permission_used",
    "has_permission",
    "method",
    "path",
    "query_params",
    "request_body",
    "user_agent",
    "referer",
    "ip_address",
    "status_code",
    "response_time_ms",
    "response_size_bytes",
    "resource_type",
    "resource_id",
    "group_id",
    "error_message",
    "error_type",
    "metadata",
]

EXECUTION_COLUMNS = [
    "log_id",
    "timestamp",
    "execution_id",
    "event",
    "function_name",
    "step_id",
    "input_data",
    "output_data",
    "error",
    "duration_ms",
    "status",
]

_COLUMNS = {"request_logs": REQUEST_COLUMNS, "execution_logs": EXECUTION_COLUMNS}

# What a ClickHouse-backed read endpoint answers when it can't serve: always
# 503 with one of these details, never a 500 or a silently empty result. The
# console tells the two apart from `/info` (`features.clickhouse`).
DISABLED_DETAIL = (
    "Request logging is disabled on this deployment (ClickHouse not configured). "
    "Set CLICKHOUSE_HOST to enable it."
)
UNAVAILABLE_DETAIL = "Request logs are temporarily unavailable: ClickHouse could not be reached."

# Failures of the connection, not of the statement: these mean ClickHouse is
# down or unreachable, so wait before the next attempt.
_CONNECTION_ERRORS = (OperationalError, StreamFailureError, OSError)


class ClickHouseLogger:
    """Centralized ClickHouse logging service."""

    def __init__(self):
        self.client: Optional[Client] = None
        self._disabled = False
        self._retry_at = 0.0
        self._connect_lock = threading.Lock()
        self._buffers: dict[str, list[list[Any]]] = {table: [] for table in _COLUMNS}
        self._dropped = 0
        self._flusher: Optional[asyncio.Task] = None
        self._inserts: set[asyncio.Future] = set()
        if not settings.clickhouse_host:
            self._initialize_client()  # logs that it's off

    def _initialize_client(self):
        """Initialize ClickHouse client connection.

        An empty CLICKHOUSE_HOST means ClickHouse is deliberately disabled
        (e.g. the compact deployment profile): skip connecting entirely so
        `_ensure_client` never attempts a connection or logs an error.
        Logging methods no-op and log queries return empty, as with any
        unavailable ClickHouse.
        """
        if not settings.clickhouse_host:
            if not self._disabled:
                logger.info("ClickHouse disabled (CLICKHOUSE_HOST empty) — request/execution logging off")
            self._disabled = True
            self.client = None
            return
        try:
            self.client = clickhouse_connect.get_client(
                host=settings.clickhouse_host,
                port=settings.clickhouse_port,
                username=settings.clickhouse_user,
                password=settings.clickhouse_password,
                database=settings.clickhouse_database,
                connect_timeout=CONNECT_TIMEOUT_S,
                # No session: the client is shared by threads, and one
                # session refuses concurrent queries.
                autogenerate_session_id=False,
            )
        except Exception as e:
            logger.error(f"Failed to initialize ClickHouse client: {e}")
            self.client = None

    def _ensure_client(self) -> bool:
        """Connect if needed. Blocking: call it from a thread."""
        return self._connected() is not None

    def _connected(self) -> Optional[Client]:
        """The client, connecting first if needed; None when unavailable.

        Blocking: call it from a thread. After a failure, it doesn't try
        again for RETRY_AFTER_S. Callers use the client it returns, not
        `self.client`, which another thread may clear meanwhile.
        """
        if self._disabled:
            return None
        with self._connect_lock:
            if self.client is None and time.monotonic() >= self._retry_at:
                self._initialize_client()
                if self.client is None and not self._disabled:
                    self._retry_at = time.monotonic() + RETRY_AFTER_S
            return self.client

    def _lost(self, client: Client, error: Exception, what: str) -> None:
        """A call on `client` failed on the connection: reconnect later,
        not at once."""
        logger.warning(f"ClickHouse {what} failed: {error}")
        with self._connect_lock:
            if self.client is client:
                self.client = None
                self._retry_at = time.monotonic() + RETRY_AFTER_S

    # --- writes: buffered, inserted in batches off the event loop ---

    def _enqueue(self, table: str, row: list[Any]) -> None:
        if self._disabled:
            return
        buffer = self._buffers[table]
        if len(buffer) >= MAX_BUFFERED_ROWS:
            self._dropped += 1
            if self._dropped % 1000 == 1:
                logger.warning(f"ClickHouse log buffer full: {self._dropped} rows dropped so far")
            return
        buffer.append(row)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # flushed by the next call that has a loop, or on close
        if self._flusher is None or self._flusher.done() or self._flusher.get_loop() is not loop:
            self._flusher = loop.create_task(self._flush_soon())

    async def _flush_soon(self) -> None:
        """Flush every FLUSH_INTERVAL_S while there are rows; stop when idle."""
        while any(self._buffers.values()):
            await asyncio.sleep(FLUSH_INTERVAL_S)
            await self.flush()

    async def flush(self) -> None:
        """Insert everything buffered so far, from a thread.

        Each insert runs as its own task, shielded: cancelling a flush (at
        shutdown) doesn't abandon rows already taken from the buffer, and
        `aclose` waits for them.
        """
        for table, rows in list(self._buffers.items()):
            if rows:
                self._buffers[table] = []
                task = asyncio.ensure_future(asyncio.to_thread(self._insert, table, rows))
                self._inserts.add(task)
                task.add_done_callback(self._inserts.discard)
                await asyncio.shield(task)

    def _insert(self, table: str, rows: list[list[Any]]) -> None:
        client = self._connected()
        if client is None:
            return
        try:
            client.insert(table, rows, column_names=_COLUMNS[table])
        except _CONNECTION_ERRORS as e:
            self._lost(client, e, f"insert of {len(rows)} {table} rows")
        except Exception as e:
            logger.warning(f"ClickHouse insert of {len(rows)} {table} rows failed: {e}")

    # --- reads: run in a thread ---

    def _query_rows(self, query: str, parameters: Optional[dict[str, Any]] = None) -> list:
        client = self._connected()
        if client is None:
            raise ConnectionError("ClickHouse not available")
        try:
            return client.query(query, parameters=parameters or {}).result_rows
        except _CONNECTION_ERRORS as e:
            self._lost(client, e, "query")
            raise ConnectionError("ClickHouse not available") from e

    @property
    def enabled(self) -> bool:
        """Whether ClickHouse is configured at all (CLICKHOUSE_HOST non-empty).
        False is a deliberate deployment choice, not an outage."""
        return not self._disabled

    @property
    def available(self) -> bool:
        """Whether ClickHouse is configured and not known to be down."""
        return not self._disabled and (self.client is not None or time.monotonic() >= self._retry_at)

    async def query(self, query: str, parameters: Optional[dict[str, Any]] = None) -> list:
        """Rows of a query, run in a thread. Raises ConnectionError when
        ClickHouse is unavailable."""
        return await asyncio.to_thread(self._query_rows, query, parameters)

    async def log_request(
        self,
        request_id: str,
        user_id: Optional[str],
        user_email: Optional[str],
        permission_used: Optional[str],
        has_permission: bool,
        method: str,
        path: str,
        query_params: dict[str, Any],
        request_body: Optional[dict[str, Any]],
        user_agent: Optional[str],
        referer: Optional[str],
        ip_address: Optional[str],
        status_code: int,
        response_time_ms: int,
        response_size_bytes: int,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        group_id: Optional[str] = None,
        error_message: Optional[str] = None,
        error_type: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ):
        """
        Log a request to ClickHouse.

        Args:
            request_id: Unique request ID
            user_id: User ID making the request
            user_email: User email
            permission_used: Permission checked (e.g., "sinas.functions.read:own")
            has_permission: Whether user had the required permission
            method: HTTP method
            path: Request path
            query_params: Query parameters dict
            request_body: Request body dict
            user_agent: User agent string
            referer: Referer header
            ip_address: Client IP address
            status_code: HTTP status code
            response_time_ms: Response time in milliseconds
            response_size_bytes: Response size in bytes
            resource_type: Type of resource accessed (e.g., "function", "chat")
            resource_id: ID of specific resource
            group_id: Group ID if applicable
            error_message: Error message if any
            error_type: Error type/class
            metadata: Additional metadata as dict
        """
        try:
            self._enqueue(
                "request_logs",
                [
                    request_id,
                    datetime.utcnow(),
                    user_id or "",
                    user_email or "",
                    permission_used or "",
                    has_permission,
                    method,
                    path,
                    json.dumps(query_params) if query_params else "",
                    json.dumps(request_body) if request_body else "",
                    user_agent or "",
                    referer or "",
                    ip_address or "",
                    status_code,
                    response_time_ms,
                    response_size_bytes,
                    resource_type or "",
                    resource_id or "",
                    group_id or "",
                    error_message or "",
                    error_type or "",
                    json.dumps(metadata) if metadata else "",
                ],
            )
        except Exception as e:
            # Logging must never fail a request
            logger.warning(f"Failed to log request: {e}")

    async def query_logs(
        self,
        user_id: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        permission: Optional[str] = None,
        path_pattern: Optional[str] = None,
        status_code: Optional[int] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list:
        """
        Query request logs with filters.

        Args:
            user_id: Filter by user ID
            start_time: Start of time range
            end_time: End of time range
            permission: Filter by permission
            path_pattern: Filter by path pattern (SQL LIKE)
            status_code: Filter by status code
            limit: Max results
            offset: Offset for pagination

        Returns:
            List of log entries. Empty when ClickHouse is disabled; raises
            ConnectionError when it is configured but unreachable, so callers
            can say so instead of showing an empty list.
        """
        if self._disabled:
            return []

        # Build WHERE conditions with parameterized queries
        conditions = []
        parameters = {}

        if user_id:
            conditions.append("user_id = {user_id:String}")
            parameters["user_id"] = user_id
        if start_time:
            conditions.append("timestamp >= {start_time:DateTime}")
            parameters["start_time"] = start_time
        if end_time:
            conditions.append("timestamp <= {end_time:DateTime}")
            parameters["end_time"] = end_time
        if permission:
            conditions.append("permission_used = {permission:String}")
            parameters["permission"] = permission
        if path_pattern:
            conditions.append("path LIKE {path_pattern:String}")
            parameters["path_pattern"] = path_pattern
        if status_code:
            conditions.append("status_code = {status_code:Int32}")
            parameters["status_code"] = status_code

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        query = f"""
            SELECT *
            FROM request_logs
            WHERE {where_clause}
            ORDER BY timestamp DESC
            LIMIT {limit}
            OFFSET {offset}
        """

        return await self.query(query, parameters)

    def _execution_event(
        self,
        execution_id: str,
        event: str,
        function_name: str = "",
        step_id: str = "",
        input_data: Optional[dict[str, Any]] = None,
        output_data: Optional[dict[str, Any]] = None,
        error: Optional[str] = None,
        duration_ms: Optional[int] = None,
        status: str = "",
    ) -> None:
        try:
            row = [
                str(uuid.uuid4()),
                datetime.utcnow(),
                execution_id,
                event,
                function_name,
                step_id,
                json.dumps(input_data, default=str) if input_data else "",
                json.dumps(output_data, default=str) if output_data else "",
                error or "",
                duration_ms or 0,
                status,
            ]
        except Exception as e:
            logger.warning(f"Failed to log {event}: {e}")
            return
        self._enqueue("execution_logs", row)

    async def log_execution_start(
        self, execution_id: str, function_name: str, input_data: dict[str, Any]
    ):
        """Log execution start event."""
        self._execution_event(execution_id, "execution_started", function_name, input_data=input_data)

    async def log_execution_end(
        self,
        execution_id: str,
        status: str,
        output_data: Optional[dict[str, Any]] = None,
        error: Optional[str] = None,
        duration_ms: Optional[int] = None,
    ):
        """Log execution end event."""
        self._execution_event(
            execution_id, "execution_completed",
            output_data=output_data, error=error, duration_ms=duration_ms, status=status,
        )

    async def log_function_call(
        self, execution_id: str, function_name: str, step_id: str, input_data: dict[str, Any]
    ):
        """Log function call within an execution."""
        self._execution_event(execution_id, "function_called", function_name, step_id, input_data=input_data)

    async def log_function_result(
        self,
        execution_id: str,
        function_name: str,
        step_id: str,
        output_data: Optional[dict[str, Any]] = None,
        error: Optional[str] = None,
        duration_ms: Optional[int] = None,
    ):
        """Log function result within an execution."""
        self._execution_event(
            execution_id, "function_completed", function_name, step_id,
            output_data=output_data, error=error, duration_ms=duration_ms,
        )

    async def get_execution_logs(self, execution_id: str, limit: int = 1000) -> list:
        """Get logs for a specific execution."""
        if self._disabled:
            return []

        try:
            query = f"""
                SELECT *
                FROM execution_logs
                WHERE execution_id = {{execution_id:String}}
                ORDER BY timestamp ASC
                LIMIT {limit}
            """
            return await self.query(query, {"execution_id": execution_id})
        except Exception as e:
            logger.error(f"Failed to get execution logs: {e}")
            return []

    def apply_storage_migration(self):
        """Apply TTL and storage policy migration to ClickHouse tables.

        If a 'tiered' storage policy exists (S3 configured), sets TTL to move
        data from hot (local) to cold (S3) after hot_retention_days.
        Otherwise, sets TTL to DELETE data after retention_days.
        """
        client = self._connected()
        if client is None:
            return

        tables = ["request_logs", "execution_logs"]
        hot_days = settings.clickhouse_hot_retention_days
        retention_days = settings.clickhouse_retention_days

        try:
            result = client.query(
                "SELECT policy_name FROM system.storage_policies WHERE policy_name = 'tiered'"
            )
            has_tiered = len(result.result_rows) > 0
        except Exception as e:
            logger.error(f"ClickHouse storage migration: failed to check storage policies: {e}")
            return

        try:
            if has_tiered:
                logger.info(
                    f"ClickHouse storage migration: tiered policy found. "
                    f"Hot retention: {hot_days}d, then move to S3."
                )
                for table in tables:
                    client.command(
                        f"ALTER TABLE {table} MODIFY SETTING storage_policy = 'tiered'"
                    )
                    client.command(
                        f"ALTER TABLE {table} MODIFY TTL CAST(timestamp AS DateTime) + INTERVAL {hot_days} DAY TO VOLUME 'cold'"
                    )
                    client.command(f"ALTER TABLE {table} MATERIALIZE TTL")
                    logger.info(f"  {table}: tiered TTL applied")
            else:
                logger.info(
                    f"ClickHouse storage migration: no S3 configured. "
                    f"TTL DELETE after {retention_days}d."
                )
                for table in tables:
                    client.command(
                        f"ALTER TABLE {table} MODIFY TTL CAST(timestamp AS DateTime) + INTERVAL {retention_days} DAY DELETE"
                    )
                    client.command(f"ALTER TABLE {table} MATERIALIZE TTL")
                    logger.info(f"  {table}: delete TTL applied")
        except Exception as e:
            logger.error(f"ClickHouse storage migration: failed to apply: {e}")

    async def aclose(self):
        """Insert what's still buffered, then close the connection."""
        if self._flusher is not None and not self._flusher.done():
            self._flusher.cancel()
        try:
            # Inserts already under way finish first (they hold rows that
            # left the buffer), then the rest is written.
            if self._inserts:
                await asyncio.gather(*self._inserts, return_exceptions=True)
            await self.flush()
        except Exception as e:
            logger.warning(f"ClickHouse final flush failed: {e}")
        self.close()

    def close(self):
        """Close ClickHouse connection."""
        if self.client:
            self.client.close()
            self.client = None


# Global logger instance
clickhouse_logger = ClickHouseLogger()
