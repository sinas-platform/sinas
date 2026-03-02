"""PostgreSQL implementation of the database schema service."""

import logging
from typing import Any

import asyncpg

from app.services.database_pool import DatabasePoolManager

from .base import DatabaseSchemaService

logger = logging.getLogger(__name__)

# Allowed filter operators (whitelist to prevent SQL injection)
_ALLOWED_OPERATORS = frozenset(
    {"=", "!=", ">", "<", ">=", "<=", "LIKE", "ILIKE", "IS NULL", "IS NOT NULL"}
)


def _quote_ident(name: str) -> str:
    """Safely quote a SQL identifier (table/column/schema name).

    Uses PostgreSQL double-quote escaping: embedded quotes are doubled.
    Rejects null bytes and names exceeding 63 characters.
    """
    if not name or "\x00" in name:
        raise ValueError(f"Invalid identifier: {name!r}")
    if len(name) > 63:
        raise ValueError(f"Identifier too long (max 63 chars): {name!r}")
    escaped = name.replace('"', '""')
    return f'"{escaped}"'


def _serialize_value(val: Any) -> Any:
    """Re-use the pool manager serializer."""
    return DatabasePoolManager._serialize_value(val)


def _serialize_row(row: asyncpg.Record) -> dict[str, Any]:
    return DatabasePoolManager._serialize_row(row)


class PostgresSchemaService(DatabaseSchemaService):
    """PostgreSQL-specific schema introspection, DDL, and data browsing."""

    # ── Introspection ──────────────────────────────────────────────

    async def list_schemas(self, pool: asyncpg.Pool) -> list[dict[str, Any]]:
        sql = """
            SELECT schema_name
            FROM information_schema.schemata
            WHERE schema_name NOT LIKE 'pg_%'
              AND schema_name != 'information_schema'
            ORDER BY schema_name
        """
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql)
        return [{"schema_name": r["schema_name"]} for r in rows]

    async def list_tables(
        self, pool: asyncpg.Pool, schema: str = "public"
    ) -> list[dict[str, Any]]:
        sql = """
            SELECT
                t.table_name,
                t.table_type,
                COALESCE(s.n_live_tup, 0) AS estimated_rows,
                pg_total_relation_size(quote_ident($1) || '.' || quote_ident(t.table_name))::bigint AS size_bytes
            FROM information_schema.tables t
            LEFT JOIN pg_stat_user_tables s
                ON s.schemaname = t.table_schema AND s.relname = t.table_name
            WHERE t.table_schema = $1
              AND t.table_type = 'BASE TABLE'
            ORDER BY t.table_name
        """
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, schema)
        return [_serialize_row(r) for r in rows]

    async def get_table_detail(
        self, pool: asyncpg.Pool, table: str, schema: str = "public"
    ) -> dict[str, Any]:
        async with pool.acquire() as conn:
            # Columns
            col_sql = """
                SELECT
                    column_name,
                    data_type,
                    udt_name,
                    is_nullable,
                    column_default,
                    character_maximum_length,
                    numeric_precision,
                    numeric_scale,
                    ordinal_position
                FROM information_schema.columns
                WHERE table_schema = $1 AND table_name = $2
                ORDER BY ordinal_position
            """
            col_rows = await conn.fetch(col_sql, schema, table)

            # Constraints (PK, FK, unique, check)
            constraint_sql = """
                SELECT
                    con.conname AS constraint_name,
                    con.contype::text AS constraint_type,
                    array_agg(att.attname ORDER BY u.pos) AS columns,
                    -- FK target
                    ref_ns.nspname AS ref_schema,
                    ref_cl.relname AS ref_table,
                    (SELECT array_agg(ra.attname ORDER BY rp.pos)
                     FROM unnest(con.confkey) WITH ORDINALITY AS rp(attnum, pos)
                     JOIN pg_attribute ra ON ra.attrelid = con.confrelid AND ra.attnum = rp.attnum
                    ) AS ref_columns,
                    pg_get_constraintdef(con.oid) AS definition
                FROM pg_constraint con
                JOIN pg_class cl ON cl.oid = con.conrelid
                JOIN pg_namespace ns ON ns.oid = cl.relnamespace
                JOIN unnest(con.conkey) WITH ORDINALITY AS u(attnum, pos) ON true
                JOIN pg_attribute att ON att.attrelid = cl.oid AND att.attnum = u.attnum
                LEFT JOIN pg_class ref_cl ON ref_cl.oid = con.confrelid
                LEFT JOIN pg_namespace ref_ns ON ref_ns.oid = ref_cl.relnamespace
                WHERE ns.nspname = $1 AND cl.relname = $2
                GROUP BY con.oid, con.conname, con.contype, con.confrelid, con.confkey,
                         ref_ns.nspname, ref_cl.relname
                ORDER BY con.contype, con.conname
            """
            constraint_rows = await conn.fetch(constraint_sql, schema, table)

            # Indexes
            idx_sql = """
                SELECT
                    indexname AS index_name,
                    indexdef AS definition
                FROM pg_indexes
                WHERE schemaname = $1 AND tablename = $2
                ORDER BY indexname
            """
            idx_rows = await conn.fetch(idx_sql, schema, table)

        # Map constraint type codes
        type_map = {"p": "PRIMARY KEY", "f": "FOREIGN KEY", "u": "UNIQUE", "c": "CHECK"}

        columns = [_serialize_row(r) for r in col_rows]
        constraints = []
        pk_columns: set[str] = set()
        for r in constraint_rows:
            ct = type_map.get(r["constraint_type"], r["constraint_type"])
            cols = list(r["columns"]) if r["columns"] else []
            if ct == "PRIMARY KEY":
                pk_columns.update(cols)
            entry: dict[str, Any] = {
                "constraint_name": r["constraint_name"],
                "constraint_type": ct,
                "columns": cols,
                "definition": r["definition"],
            }
            if ct == "FOREIGN KEY":
                entry["ref_schema"] = r["ref_schema"]
                entry["ref_table"] = r["ref_table"]
                entry["ref_columns"] = list(r["ref_columns"]) if r["ref_columns"] else []
            constraints.append(entry)

        indexes = [_serialize_row(r) for r in idx_rows]

        # Annotate columns with PK flag
        for col in columns:
            col["is_primary_key"] = col["column_name"] in pk_columns

        return {
            "table_name": table,
            "schema_name": schema,
            "columns": columns,
            "constraints": constraints,
            "indexes": indexes,
        }

    async def list_views(
        self, pool: asyncpg.Pool, schema: str = "public"
    ) -> list[dict[str, Any]]:
        sql = """
            SELECT table_name AS view_name, view_definition
            FROM information_schema.views
            WHERE table_schema = $1
            ORDER BY table_name
        """
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, schema)
        return [_serialize_row(r) for r in rows]

    # ── DDL ────────────────────────────────────────────────────────

    async def create_table(
        self,
        pool: asyncpg.Pool,
        table: str,
        columns: list[dict[str, Any]],
        schema: str = "public",
        if_not_exists: bool = False,
    ) -> None:
        if not columns:
            raise ValueError("At least one column is required")

        col_defs = []
        pk_cols = []
        for col in columns:
            name = _quote_ident(col["name"])
            dtype = col["type"]  # validated at schema level
            parts = [name, dtype]
            if not col.get("nullable", True):
                parts.append("NOT NULL")
            if col.get("default") is not None:
                parts.append(f"DEFAULT {col['default']}")
            col_defs.append(" ".join(parts))
            if col.get("primary_key"):
                pk_cols.append(name)

        if pk_cols:
            col_defs.append(f"PRIMARY KEY ({', '.join(pk_cols)})")

        exists = "IF NOT EXISTS " if if_not_exists else ""
        qualified = f"{_quote_ident(schema)}.{_quote_ident(table)}"
        col_list = ",\n  ".join(col_defs)
        ddl = f"CREATE TABLE {exists}{qualified} (\n  {col_list}\n)"

        async with pool.acquire() as conn:
            await conn.execute(ddl)

    async def alter_table(
        self,
        pool: asyncpg.Pool,
        table: str,
        schema: str = "public",
        add_columns: list[dict[str, Any]] | None = None,
        drop_columns: list[str] | None = None,
        rename_columns: dict[str, str] | None = None,
    ) -> None:
        qualified = f"{_quote_ident(schema)}.{_quote_ident(table)}"
        stmts: list[str] = []

        if add_columns:
            for col in add_columns:
                name = _quote_ident(col["name"])
                dtype = col["type"]
                parts = [f"ADD COLUMN {name} {dtype}"]
                if not col.get("nullable", True):
                    parts.append("NOT NULL")
                if col.get("default") is not None:
                    parts.append(f"DEFAULT {col['default']}")
                stmts.append(f"ALTER TABLE {qualified} {' '.join(parts)}")

        if drop_columns:
            for col_name in drop_columns:
                stmts.append(f"ALTER TABLE {qualified} DROP COLUMN {_quote_ident(col_name)}")

        if rename_columns:
            for old_name, new_name in rename_columns.items():
                stmts.append(
                    f"ALTER TABLE {qualified} RENAME COLUMN {_quote_ident(old_name)} TO {_quote_ident(new_name)}"
                )

        if not stmts:
            return

        async with pool.acquire() as conn:
            async with conn.transaction():
                for stmt in stmts:
                    await conn.execute(stmt)

    async def drop_table(
        self,
        pool: asyncpg.Pool,
        table: str,
        schema: str = "public",
        cascade: bool = False,
        if_exists: bool = False,
    ) -> None:
        exists = "IF EXISTS " if if_exists else ""
        casc = " CASCADE" if cascade else ""
        qualified = f"{_quote_ident(schema)}.{_quote_ident(table)}"
        ddl = f"DROP TABLE {exists}{qualified}{casc}"
        async with pool.acquire() as conn:
            await conn.execute(ddl)

    async def create_view(
        self,
        pool: asyncpg.Pool,
        name: str,
        sql: str,
        schema: str = "public",
        or_replace: bool = False,
    ) -> None:
        replace = "OR REPLACE " if or_replace else ""
        qualified = f"{_quote_ident(schema)}.{_quote_ident(name)}"
        ddl = f"CREATE {replace}VIEW {qualified} AS {sql}"
        async with pool.acquire() as conn:
            await conn.execute(ddl)

    async def drop_view(
        self,
        pool: asyncpg.Pool,
        name: str,
        schema: str = "public",
        cascade: bool = False,
        if_exists: bool = False,
    ) -> None:
        exists = "IF EXISTS " if if_exists else ""
        casc = " CASCADE" if cascade else ""
        qualified = f"{_quote_ident(schema)}.{_quote_ident(name)}"
        ddl = f"DROP VIEW {exists}{qualified}{casc}"
        async with pool.acquire() as conn:
            await conn.execute(ddl)

    # ── Data Browser ───────────────────────────────────────────────

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
        qualified = f"{_quote_ident(schema)}.{_quote_ident(table)}"
        where_clauses: list[str] = []
        params: list[Any] = []
        idx = 1

        if filters:
            for f in filters:
                op = f["operator"].upper()
                if op not in _ALLOWED_OPERATORS:
                    raise ValueError(f"Unsupported filter operator: {op}")
                col = _quote_ident(f["column"])
                if op in ("IS NULL", "IS NOT NULL"):
                    where_clauses.append(f"{col} {op}")
                else:
                    where_clauses.append(f"{col} {op} ${idx}")
                    params.append(f["value"])
                    idx += 1

        where = ""
        if where_clauses:
            where = " WHERE " + " AND ".join(where_clauses)

        # Count query
        count_sql = f"SELECT count(*) AS total FROM {qualified}{where}"

        # Data query
        order = ""
        if sort_by:
            direction = "DESC" if sort_order.upper() == "DESC" else "ASC"
            order = f" ORDER BY {_quote_ident(sort_by)} {direction}"

        data_sql = f"SELECT * FROM {qualified}{where}{order} LIMIT ${idx} OFFSET ${idx + 1}"
        params.extend([limit, offset])

        async with pool.acquire() as conn:
            total = await conn.fetchval(count_sql, *params[: idx - 1])
            rows = await conn.fetch(data_sql, *params)

        return {
            "rows": [_serialize_row(r) for r in rows],
            "total_count": total,
        }

    async def insert_rows(
        self,
        pool: asyncpg.Pool,
        table: str,
        rows: list[dict[str, Any]],
        schema: str = "public",
    ) -> list[dict[str, Any]]:
        if not rows:
            return []

        qualified = f"{_quote_ident(schema)}.{_quote_ident(table)}"
        # Use column names from first row
        col_names = list(rows[0].keys())
        quoted_cols = [_quote_ident(c) for c in col_names]

        inserted: list[dict[str, Any]] = []
        async with pool.acquire() as conn:
            async with conn.transaction():
                for row in rows:
                    placeholders = [f"${i + 1}" for i in range(len(col_names))]
                    sql = (
                        f"INSERT INTO {qualified} ({', '.join(quoted_cols)}) "
                        f"VALUES ({', '.join(placeholders)}) RETURNING *"
                    )
                    values = [row.get(c) for c in col_names]
                    result = await conn.fetchrow(sql, *values)
                    if result:
                        inserted.append(_serialize_row(result))

        return inserted

    async def update_rows(
        self,
        pool: asyncpg.Pool,
        table: str,
        where: dict[str, Any],
        set_values: dict[str, Any],
        schema: str = "public",
    ) -> int:
        if not where:
            raise ValueError("WHERE conditions are required for UPDATE")
        if not set_values:
            raise ValueError("SET values are required for UPDATE")

        qualified = f"{_quote_ident(schema)}.{_quote_ident(table)}"
        params: list[Any] = []
        idx = 1

        set_parts = []
        for col, val in set_values.items():
            set_parts.append(f"{_quote_ident(col)} = ${idx}")
            params.append(val)
            idx += 1

        where_parts = []
        for col, val in where.items():
            if val is None:
                where_parts.append(f"{_quote_ident(col)} IS NULL")
            else:
                where_parts.append(f"{_quote_ident(col)} = ${idx}")
                params.append(val)
                idx += 1

        sql = (
            f"UPDATE {qualified} SET {', '.join(set_parts)} "
            f"WHERE {' AND '.join(where_parts)}"
        )

        async with pool.acquire() as conn:
            result = await conn.execute(sql, *params)

        # asyncpg returns e.g. "UPDATE 3"
        try:
            return int(result.split()[-1])
        except (ValueError, IndexError):
            return 0

    async def delete_rows(
        self,
        pool: asyncpg.Pool,
        table: str,
        where: dict[str, Any],
        schema: str = "public",
    ) -> int:
        if not where:
            raise ValueError("WHERE conditions are required for DELETE")

        qualified = f"{_quote_ident(schema)}.{_quote_ident(table)}"
        params: list[Any] = []
        idx = 1

        where_parts = []
        for col, val in where.items():
            if val is None:
                where_parts.append(f"{_quote_ident(col)} IS NULL")
            else:
                where_parts.append(f"{_quote_ident(col)} = ${idx}")
                params.append(val)
                idx += 1

        sql = f"DELETE FROM {qualified} WHERE {' AND '.join(where_parts)}"

        async with pool.acquire() as conn:
            result = await conn.execute(sql, *params)

        try:
            return int(result.split()[-1])
        except (ValueError, IndexError):
            return 0
