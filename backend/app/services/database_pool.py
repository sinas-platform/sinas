"""Database connection pool manager for external database queries."""
import datetime
import hashlib
import json
import logging
import re
import uuid
from decimal import Decimal
from typing import Any, Optional

import asyncpg
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import EncryptionService
from app.models.database_connection import DatabaseConnection

logger = logging.getLogger(__name__)


class DatabasePoolManager:
    """Singleton managing asyncpg pools per database_connection_id."""

    _instance: Optional["DatabasePoolManager"] = None

    def __init__(self):
        self._pools: dict[str, asyncpg.Pool] = {}  # connection_id -> pool
        self._pool_checksums: dict[str, str] = {}  # connection_id -> config hash

    @classmethod
    def get_instance(cls) -> "DatabasePoolManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _config_hash(self, conn: DatabaseConnection) -> str:
        """Calculate hash of connection config for invalidation detection."""
        data = {
            "host": conn.host,
            "port": conn.port,
            "database": conn.database,
            "username": conn.username,
            "password": conn.password,  # encrypted value
            "ssl_mode": conn.ssl_mode,
            "config": conn.config,
        }
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    async def get_pool(self, db: AsyncSession, connection_id: str) -> asyncpg.Pool:
        """Get or create a connection pool for the given connection_id."""
        from sqlalchemy import select

        # Load connection config
        result = await db.execute(
            select(DatabaseConnection).where(
                DatabaseConnection.id == connection_id,
                DatabaseConnection.is_active == True,
            )
        )
        conn = result.scalar_one_or_none()
        if not conn:
            raise ValueError(f"Database connection '{connection_id}' not found or inactive")

        config_hash = self._config_hash(conn)

        # Check if pool exists and config hasn't changed
        if connection_id in self._pools and self._pool_checksums.get(connection_id) == config_hash:
            return self._pools[connection_id]

        # Config changed or new pool needed - close old pool if exists
        if connection_id in self._pools:
            try:
                await self._pools[connection_id].close()
            except Exception as e:
                logger.warning(f"Error closing stale pool for {connection_id}: {e}")

        # Decrypt password
        decrypted_password = None
        if conn.password:
            encryption_service = EncryptionService()
            decrypted_password = encryption_service.decrypt(conn.password)

        # Pool settings from config
        pool_config = conn.config or {}
        min_size = pool_config.get("min_pool_size", 2)
        max_size = pool_config.get("max_pool_size", 10)

        ssl_mode = conn.ssl_mode or "prefer"

        pool = await asyncpg.create_pool(
            host=conn.host,
            port=conn.port,
            database=conn.database,
            user=conn.username,
            password=decrypted_password,
            ssl=ssl_mode,
            min_size=min_size,
            max_size=max_size,
        )

        self._pools[connection_id] = pool
        self._pool_checksums[connection_id] = config_hash

        logger.info(f"Created connection pool for database connection {conn.name}")
        return pool

    async def invalidate(self, connection_id: str):
        """Close and remove pool for the given connection_id."""
        if connection_id in self._pools:
            try:
                await self._pools[connection_id].close()
            except Exception as e:
                logger.warning(f"Error closing pool for {connection_id}: {e}")
            del self._pools[connection_id]
            self._pool_checksums.pop(connection_id, None)
            logger.info(f"Invalidated pool for connection {connection_id}")

    async def execute_query(
        self,
        db: AsyncSession,
        connection_id: str,
        sql: str,
        params: dict[str, Any],
        operation: str = "read",
        timeout_ms: int = 5000,
        max_rows: int = 1000,
    ) -> dict[str, Any]:
        """
        Execute a SQL query on the given connection.

        Converts :param_name syntax to $N positional params for asyncpg.
        """
        pool = await self.get_pool(db, connection_id)

        # Convert :param_name to $N positional params
        converted_sql, positional_params = self._convert_params(sql, params)

        timeout_s = timeout_ms / 1000.0

        async with pool.acquire() as conn:
            if operation == "read":
                # Add LIMIT if not present
                sql_upper = converted_sql.upper().strip()
                if "LIMIT" not in sql_upper:
                    converted_sql = f"{converted_sql} LIMIT {max_rows}"

                rows = await conn.fetch(converted_sql, *positional_params, timeout=timeout_s)
                return {
                    "rows": [self._serialize_row(row) for row in rows],
                    "row_count": len(rows),
                }
            else:
                result = await conn.execute(converted_sql, *positional_params, timeout=timeout_s)
                # asyncpg returns string like "UPDATE 5"
                affected = 0
                if result and " " in result:
                    try:
                        affected = int(result.split(" ")[-1])
                    except ValueError:
                        pass
                return {
                    "affected_rows": affected,
                }

    @staticmethod
    def _serialize_value(val: Any) -> Any:
        """Convert non-JSON-serializable types to JSON-safe values."""
        if val is None:
            return None
        if isinstance(val, (uuid.UUID, Decimal)):
            return str(val)
        if isinstance(val, (datetime.datetime, datetime.date, datetime.time)):
            return val.isoformat()
        if isinstance(val, datetime.timedelta):
            return val.total_seconds()
        if isinstance(val, bytes):
            return val.hex()
        if isinstance(val, (list, tuple)):
            return [DatabasePoolManager._serialize_value(v) for v in val]
        if isinstance(val, dict):
            return {k: DatabasePoolManager._serialize_value(v) for k, v in val.items()}
        return val

    @staticmethod
    def _serialize_row(row: asyncpg.Record) -> dict[str, Any]:
        """Convert an asyncpg Record to a JSON-safe dict."""
        return {key: DatabasePoolManager._serialize_value(row[key]) for key in row.keys()}

    @staticmethod
    def _convert_params(sql: str, params: dict[str, Any]) -> tuple[str, list[Any]]:
        """
        Convert :param_name placeholders to $N positional params.

        Returns (converted_sql, positional_params_list).
        """
        # Find all :param_name occurrences (not ::type_cast)
        pattern = re.compile(r"(?<!:):([a-zA-Z_][a-zA-Z0-9_]*)")
        param_names = pattern.findall(sql)

        if not param_names:
            return sql, []

        # Build ordered unique param list preserving first occurrence order
        seen = set()
        ordered_params = []
        for name in param_names:
            if name not in seen:
                seen.add(name)
                ordered_params.append(name)

        # Build name -> position mapping
        name_to_pos = {name: idx + 1 for idx, name in enumerate(ordered_params)}

        # Replace :name with $N
        def replacer(match):
            name = match.group(1)
            return f"${name_to_pos[name]}"

        converted_sql = pattern.sub(replacer, sql)

        # Build positional params list
        positional_params = [params.get(name) for name in ordered_params]

        return converted_sql, positional_params

    async def close_all(self):
        """Close all connection pools. Called on shutdown."""
        for connection_id, pool in list(self._pools.items()):
            try:
                await pool.close()
            except Exception as e:
                logger.warning(f"Error closing pool for {connection_id}: {e}")
        self._pools.clear()
        self._pool_checksums.clear()
        logger.info("All database connection pools closed")
