"""ClickHouse logger service for comprehensive request logging."""
import json
import uuid
from datetime import datetime
from typing import Any, Optional

import clickhouse_connect
from clickhouse_connect.driver.client import Client

from app.core.config import settings


class ClickHouseLogger:
    """Centralized ClickHouse logging service."""

    def __init__(self):
        self.client: Optional[Client] = None
        self._initialize_client()

    def _initialize_client(self):
        """Initialize ClickHouse client connection."""
        try:
            self.client = clickhouse_connect.get_client(
                host=settings.clickhouse_host,
                port=settings.clickhouse_port,
                username=settings.clickhouse_user,
                password=settings.clickhouse_password,
                database=settings.clickhouse_database,
            )
        except Exception as e:
            print(f"Failed to initialize ClickHouse client: {e}")
            self.client = None

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
        if not self.client:
            return  # Skip logging if ClickHouse not available

        try:
            # Serialize complex fields to JSON
            query_params_str = json.dumps(query_params) if query_params else ""
            request_body_str = json.dumps(request_body) if request_body else ""
            metadata_str = json.dumps(metadata) if metadata else ""

            # Insert log entry
            self.client.insert(
                "request_logs",
                [
                    [
                        request_id,
                        datetime.utcnow(),
                        user_id or "",
                        user_email or "",
                        permission_used or "",
                        has_permission,
                        method,
                        path,
                        query_params_str,
                        request_body_str,
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
                        metadata_str,
                    ]
                ],
                column_names=[
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
                ],
            )
        except Exception as e:
            # Silently fail - logging should never crash the app
            # Only print if in debug mode
            if settings.debug:
                print(f"Failed to log request to ClickHouse: {e}")

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
            List of log entries
        """
        if not self.client:
            return []

        try:
            # Build WHERE conditions
            conditions = []
            if user_id:
                conditions.append(f"user_id = '{user_id}'")
            if start_time:
                conditions.append(f"timestamp >= '{start_time.isoformat()}'")
            if end_time:
                conditions.append(f"timestamp <= '{end_time.isoformat()}'")
            if permission:
                conditions.append(f"permission_used = '{permission}'")
            if path_pattern:
                conditions.append(f"path LIKE '{path_pattern}'")
            if status_code:
                conditions.append(f"status_code = {status_code}")

            where_clause = " AND ".join(conditions) if conditions else "1=1"

            query = f"""
                SELECT *
                FROM request_logs
                WHERE {where_clause}
                ORDER BY timestamp DESC
                LIMIT {limit}
                OFFSET {offset}
            """

            result = self.client.query(query)
            return result.result_rows
        except Exception as e:
            print(f"Failed to query logs from ClickHouse: {e}")
            return []

    async def log_execution_start(
        self, execution_id: str, function_name: str, input_data: dict[str, Any]
    ):
        """Log execution start event."""
        if not self.client:
            return

        try:
            self.client.insert(
                "execution_logs",
                [
                    [
                        str(uuid.uuid4()),
                        datetime.utcnow(),
                        execution_id,
                        "execution_started",
                        function_name,
                        "",  # step_id
                        json.dumps(input_data) if input_data else "",
                        "",  # output_data
                        "",  # error
                        0,  # duration_ms
                        "",  # status
                    ]
                ],
                column_names=[
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
                ],
            )
        except Exception as e:
            if settings.debug:
                print(f"Failed to log execution start: {e}")

    async def log_execution_end(
        self,
        execution_id: str,
        status: str,
        output_data: Optional[dict[str, Any]] = None,
        error: Optional[str] = None,
        duration_ms: Optional[int] = None,
    ):
        """Log execution end event."""
        if not self.client:
            return

        try:
            self.client.insert(
                "execution_logs",
                [
                    [
                        str(uuid.uuid4()),
                        datetime.utcnow(),
                        execution_id,
                        "execution_completed",
                        "",  # function_name
                        "",  # step_id
                        "",  # input_data
                        json.dumps(output_data) if output_data else "",
                        error or "",
                        duration_ms or 0,
                        status,
                    ]
                ],
                column_names=[
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
                ],
            )
        except Exception as e:
            if settings.debug:
                print(f"Failed to log execution end: {e}")

    async def log_function_call(
        self, execution_id: str, function_name: str, step_id: str, input_data: dict[str, Any]
    ):
        """Log function call within an execution."""
        if not self.client:
            return

        try:
            self.client.insert(
                "execution_logs",
                [
                    [
                        str(uuid.uuid4()),
                        datetime.utcnow(),
                        execution_id,
                        "function_called",
                        function_name,
                        step_id,
                        json.dumps(input_data) if input_data else "",
                        "",  # output_data
                        "",  # error
                        0,  # duration_ms
                        "",  # status
                    ]
                ],
                column_names=[
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
                ],
            )
        except Exception as e:
            if settings.debug:
                print(f"Failed to log function call: {e}")

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
        if not self.client:
            return

        try:
            self.client.insert(
                "execution_logs",
                [
                    [
                        str(uuid.uuid4()),
                        datetime.utcnow(),
                        execution_id,
                        "function_completed",
                        function_name,
                        step_id,
                        "",  # input_data
                        json.dumps(output_data) if output_data else "",
                        error or "",
                        duration_ms or 0,
                        "",  # status
                    ]
                ],
                column_names=[
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
                ],
            )
        except Exception as e:
            if settings.debug:
                print(f"Failed to log function result: {e}")

    async def get_execution_logs(self, execution_id: str, limit: int = 1000) -> list:
        """Get logs for a specific execution."""
        if not self.client:
            return []

        try:
            query = f"""
                SELECT *
                FROM execution_logs
                WHERE execution_id = '{execution_id}'
                ORDER BY timestamp ASC
                LIMIT {limit}
            """
            result = self.client.query(query)
            return result.result_rows
        except Exception as e:
            print(f"Failed to get execution logs: {e}")
            return []

    def apply_storage_migration(self):
        """Apply TTL and storage policy migration to ClickHouse tables.

        If a 'tiered' storage policy exists (S3 configured), sets TTL to move
        data from hot (local) to cold (S3) after hot_retention_days.
        Otherwise, sets TTL to DELETE data after retention_days.
        """
        if not self.client:
            return

        tables = ["request_logs", "execution_logs"]
        hot_days = settings.clickhouse_hot_retention_days
        retention_days = settings.clickhouse_retention_days

        try:
            result = self.client.query(
                "SELECT policy_name FROM system.storage_policies WHERE policy_name = 'tiered'"
            )
            has_tiered = len(result.result_rows) > 0
        except Exception as e:
            print(f"ClickHouse storage migration: failed to check storage policies: {e}")
            return

        try:
            if has_tiered:
                print(
                    f"ClickHouse storage migration: tiered policy found. "
                    f"Hot retention: {hot_days}d, then move to S3."
                )
                for table in tables:
                    self.client.command(
                        f"ALTER TABLE {table} MODIFY SETTING storage_policy = 'tiered'"
                    )
                    self.client.command(
                        f"ALTER TABLE {table} MODIFY TTL timestamp + INTERVAL {hot_days} DAY TO VOLUME 'cold'"
                    )
                    self.client.command(f"ALTER TABLE {table} MATERIALIZE TTL")
                    print(f"  {table}: tiered TTL applied")
            else:
                print(
                    f"ClickHouse storage migration: no S3 configured. "
                    f"TTL DELETE after {retention_days}d."
                )
                for table in tables:
                    self.client.command(
                        f"ALTER TABLE {table} MODIFY TTL timestamp + INTERVAL {retention_days} DAY DELETE"
                    )
                    self.client.command(f"ALTER TABLE {table} MATERIALIZE TTL")
                    print(f"  {table}: delete TTL applied")
        except Exception as e:
            print(f"ClickHouse storage migration: failed to apply: {e}")

    def close(self):
        """Close ClickHouse connection."""
        if self.client:
            self.client.close()


# Global logger instance
clickhouse_logger = ClickHouseLogger()
