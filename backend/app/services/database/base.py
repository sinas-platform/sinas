"""Abstract base class for database schema services."""

from abc import ABC, abstractmethod
from typing import Any

import asyncpg


class DatabaseSchemaService(ABC):
    """Abstract interface for database schema introspection, DDL, and data browsing."""

    # ── Introspection ──────────────────────────────────────────────

    @abstractmethod
    async def list_schemas(self, pool: asyncpg.Pool) -> list[dict[str, Any]]:
        """List all user schemas (excluding system schemas)."""
        ...

    @abstractmethod
    async def list_tables(
        self, pool: asyncpg.Pool, schema: str = "public"
    ) -> list[dict[str, Any]]:
        """List tables in a schema with row estimates and sizes."""
        ...

    @abstractmethod
    async def get_table_detail(
        self, pool: asyncpg.Pool, table: str, schema: str = "public"
    ) -> dict[str, Any]:
        """Get detailed table info: columns, constraints, indexes."""
        ...

    @abstractmethod
    async def list_views(
        self, pool: asyncpg.Pool, schema: str = "public"
    ) -> list[dict[str, Any]]:
        """List views in a schema."""
        ...

    # ── DDL ────────────────────────────────────────────────────────

    @abstractmethod
    async def create_table(
        self,
        pool: asyncpg.Pool,
        table: str,
        columns: list[dict[str, Any]],
        schema: str = "public",
        if_not_exists: bool = False,
    ) -> None:
        """Create a new table."""
        ...

    @abstractmethod
    async def alter_table(
        self,
        pool: asyncpg.Pool,
        table: str,
        schema: str = "public",
        add_columns: list[dict[str, Any]] | None = None,
        drop_columns: list[str] | None = None,
        rename_columns: dict[str, str] | None = None,
    ) -> None:
        """Alter a table: add/drop/rename columns."""
        ...

    @abstractmethod
    async def drop_table(
        self,
        pool: asyncpg.Pool,
        table: str,
        schema: str = "public",
        cascade: bool = False,
        if_exists: bool = False,
    ) -> None:
        """Drop a table."""
        ...

    @abstractmethod
    async def create_view(
        self,
        pool: asyncpg.Pool,
        name: str,
        sql: str,
        schema: str = "public",
        or_replace: bool = False,
    ) -> None:
        """Create a view."""
        ...

    @abstractmethod
    async def drop_view(
        self,
        pool: asyncpg.Pool,
        name: str,
        schema: str = "public",
        cascade: bool = False,
        if_exists: bool = False,
    ) -> None:
        """Drop a view."""
        ...

    # ── Data Browser ───────────────────────────────────────────────

    @abstractmethod
    async def browse_rows(
        self,
        pool: asyncpg.Pool,
        table: str,
        schema: str = "public",
        filters: list[dict[str, Any]] | None = None,
        sort_by: str | None = None,
        sort_order: str = "asc",
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Browse table rows with filtering, sorting, pagination. Returns rows + total_count."""
        ...

    @abstractmethod
    async def insert_rows(
        self,
        pool: asyncpg.Pool,
        table: str,
        rows: list[dict[str, Any]],
        schema: str = "public",
    ) -> list[dict[str, Any]]:
        """Insert rows, returning inserted records."""
        ...

    @abstractmethod
    async def update_rows(
        self,
        pool: asyncpg.Pool,
        table: str,
        where: dict[str, Any],
        set_values: dict[str, Any],
        schema: str = "public",
    ) -> int:
        """Update rows matching WHERE conditions. Returns affected count."""
        ...

    @abstractmethod
    async def delete_rows(
        self,
        pool: asyncpg.Pool,
        table: str,
        where: dict[str, Any],
        schema: str = "public",
    ) -> int:
        """Delete rows matching WHERE conditions. Returns affected count."""
        ...
