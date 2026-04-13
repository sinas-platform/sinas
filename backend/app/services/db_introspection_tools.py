"""Sinas database introspection tools for agents.

Read-only tools for inspecting table structures in DatabaseConnections.
Uses the same connection pool as regular queries. Includes table/column
annotations from the semantic layer when available.

Opt-in via `system_tools: ["databaseIntrospection"]` on the agent.

Currently supports PostgreSQL. When other adapters are added, extend
the _INTROSPECTION_SQL dict with adapter-specific queries.
"""
import logging
from typing import Any, Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database_connection import DatabaseConnection
from app.models.table_annotation import TableAnnotation
from app.services.database_pool import DatabasePoolManager

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Adapter-specific introspection SQL
# ─────────────────────────────────────────────────────────────

_INTROSPECTION_SQL: dict[str, dict[str, str]] = {
    "postgresql": {
        "list_tables": """
            SELECT
                t.table_schema,
                t.table_name,
                t.table_type,
                pg_stat.n_live_tup AS approximate_row_count
            FROM information_schema.tables t
            LEFT JOIN pg_stat_user_tables pg_stat
                ON pg_stat.schemaname = t.table_schema
                AND pg_stat.relname = t.table_name
            WHERE t.table_schema NOT IN ('pg_catalog', 'information_schema')
            ORDER BY t.table_schema, t.table_name
        """,
        "describe_table": """
            SELECT
                c.column_name,
                c.data_type,
                c.udt_name,
                c.is_nullable,
                c.column_default,
                c.character_maximum_length,
                c.numeric_precision,
                c.numeric_scale
            FROM information_schema.columns c
            WHERE c.table_schema = $1
              AND c.table_name = $2
            ORDER BY c.ordinal_position
        """,
        "list_indexes": """
            SELECT
                i.relname AS index_name,
                a.attname AS column_name,
                ix.indisunique AS is_unique,
                ix.indisprimary AS is_primary
            FROM pg_index ix
            JOIN pg_class t ON t.oid = ix.indrelid
            JOIN pg_class i ON i.oid = ix.indexrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(ix.indkey)
            WHERE n.nspname = $1
              AND t.relname = $2
            ORDER BY i.relname, a.attnum
        """,
        "foreign_keys": """
            SELECT
                kcu.column_name,
                ccu.table_schema AS foreign_table_schema,
                ccu.table_name AS foreign_table_name,
                ccu.column_name AS foreign_column_name,
                tc.constraint_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
                ON tc.constraint_name = kcu.constraint_name
                AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
                ON ccu.constraint_name = tc.constraint_name
                AND ccu.table_schema = tc.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema = $1
              AND tc.table_name = $2
            ORDER BY kcu.column_name
        """,
    },
}


# ─────────────────────────────────────────────────────────────
# Tool definitions
# ─────────────────────────────────────────────────────────────

_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "sinas_db_list_tables",
            "description": (
                "List all tables in a database connection with their schemas, "
                "types, approximate row counts, and any table-level annotations "
                "(display names, descriptions) from the semantic layer."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "connectionName": {
                        "type": "string",
                        "description": "Database connection name (e.g. 'built-in', 'analytics-db').",
                    },
                },
                "required": ["connectionName"],
            },
            "_metadata": {"system_tool": "databaseIntrospection"},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sinas_db_describe_table",
            "description": (
                "Describe a table's structure: columns with data types, "
                "nullability, defaults, indexes, foreign keys, and any "
                "column-level annotations (display names, descriptions) "
                "from the semantic layer. Schema defaults to 'public'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "table": {
                        "type": "string",
                        "description": "Table name to describe.",
                    },
                    "schema": {
                        "type": "string",
                        "description": "Schema name. Defaults to 'public'.",
                    },
                    "connectionName": {
                        "type": "string",
                        "description": "Database connection name (e.g. 'built-in', 'analytics-db').",
                    },
                },
                "required": ["table", "connectionName"],
            },
            "_metadata": {"system_tool": "databaseIntrospection"},
        },
    },
]


def get_db_introspection_tool_definitions() -> list[dict[str, Any]]:
    return [t.copy() for t in _TOOL_DEFINITIONS]


DB_INTROSPECTION_TOOL_NAMES = {t["function"]["name"] for t in _TOOL_DEFINITIONS}


def is_db_introspection_tool(tool_name: str) -> bool:
    return tool_name in DB_INTROSPECTION_TOOL_NAMES


# ─────────────────────────────────────────────────────────────
# Dispatch
# ─────────────────────────────────────────────────────────────

async def execute_db_introspection_tool(
    db: AsyncSession,
    tool_name: str,
    arguments: dict[str, Any],
    agent_system_tools: Optional[list] = None,
) -> dict[str, Any]:
    from app.services.system_tool_helpers import get_system_tool_config

    config = get_system_tool_config(agent_system_tools or [], "databaseIntrospection")
    if config is None:
        return {
            "error": "capability_not_enabled",
            "detail": "This agent does not have 'databaseIntrospection' in its systemTools.",
        }

    # Check connection access
    allowed_connections = config.get("connections")
    if allowed_connections is not None:
        connection_name = arguments.get("connectionName", "")
        if connection_name not in allowed_connections:
            return {
                "error": "connection_not_allowed",
                "detail": (
                    f"Connection '{connection_name}' is not in this agent's allowed "
                    f"connections: {allowed_connections}"
                ),
            }

    try:
        if tool_name == "sinas_db_list_tables":
            return await _list_tables(db, arguments)
        if tool_name == "sinas_db_describe_table":
            return await _describe_table(db, arguments)
        return {"error": "unknown_tool", "detail": f"Unknown tool: {tool_name}"}
    except Exception as e:
        logger.error(f"DB introspection tool {tool_name} failed: {e}", exc_info=True)
        return {"error": "internal_error", "detail": str(e)}


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

async def _resolve_connection(
    db: AsyncSession, connection_name: str
) -> tuple[DatabaseConnection, str]:
    conn = await DatabaseConnection.get_by_name(db, connection_name)
    if not conn:
        raise ValueError(f"Database connection '{connection_name}' not found or inactive")
    return conn, conn.connection_type


def _get_sql(connection_type: str, query_name: str) -> str:
    adapter = _INTROSPECTION_SQL.get(connection_type)
    if not adapter:
        raise ValueError(
            f"Database introspection not supported for '{connection_type}'. "
            f"Supported: {', '.join(_INTROSPECTION_SQL.keys())}"
        )
    sql = adapter.get(query_name)
    if not sql:
        raise ValueError(f"Introspection query '{query_name}' not available for {connection_type}")
    return sql


async def _get_annotations(
    db: AsyncSession,
    connection_id: str,
    schema_name: str,
    table_name: Optional[str] = None,
) -> dict[str, dict[str, Any]]:
    """Load annotations for tables/columns.

    Returns a dict keyed by:
      - "table:<schema>.<table>" for table-level annotations
      - "column:<schema>.<table>.<column>" for column-level annotations

    Each value has optional display_name and description.
    """
    query = select(TableAnnotation).where(
        TableAnnotation.database_connection_id == connection_id,
        TableAnnotation.schema_name == schema_name,
    )
    if table_name:
        query = query.where(TableAnnotation.table_name == table_name)

    result = await db.execute(query)
    annotations = result.scalars().all()

    out: dict[str, dict[str, Any]] = {}
    for ann in annotations:
        if ann.column_name:
            key = f"column:{ann.schema_name}.{ann.table_name}.{ann.column_name}"
        else:
            key = f"table:{ann.schema_name}.{ann.table_name}"
        entry: dict[str, Any] = {}
        if ann.display_name:
            entry["display_name"] = ann.display_name
        if ann.description:
            entry["description"] = ann.description
        if entry:
            out[key] = entry

    return out


# ─────────────────────────────────────────────────────────────
# Tool implementations
# ─────────────────────────────────────────────────────────────

async def _list_tables(db: AsyncSession, arguments: dict[str, Any]) -> dict[str, Any]:
    connection_name = arguments.get("connectionName", "")
    if not connection_name:
        return {"error": "missing_connectionName", "detail": "'connectionName' is required"}

    conn, conn_type = await _resolve_connection(db, connection_name)

    pool_manager = DatabasePoolManager.get_instance()
    pool = await pool_manager.get_pool(db, str(conn.id))

    sql = _get_sql(conn_type, "list_tables")

    async with pool.acquire() as c:
        rows = await c.fetch(sql, timeout=10)

    # Load all table-level annotations for this connection
    ann_result = await db.execute(
        select(TableAnnotation).where(
            TableAnnotation.database_connection_id == str(conn.id),
            TableAnnotation.column_name.is_(None),
        )
    )
    table_annotations: dict[str, dict[str, Any]] = {}
    for ann in ann_result.scalars().all():
        key = f"{ann.schema_name}.{ann.table_name}"
        entry: dict[str, Any] = {}
        if ann.display_name:
            entry["display_name"] = ann.display_name
        if ann.description:
            entry["description"] = ann.description
        if entry:
            table_annotations[key] = entry

    tables = []
    for row in rows:
        table: dict[str, Any] = {
            "schema": row["table_schema"],
            "table": row["table_name"],
            "type": row["table_type"],
            "approximate_rows": row["approximate_row_count"],
        }
        ann_key = f"{row['table_schema']}.{row['table_name']}"
        if ann_key in table_annotations:
            table["annotation"] = table_annotations[ann_key]
        tables.append(table)

    return {"connectionName": connection_name, "tables": tables, "count": len(tables)}


async def _describe_table(db: AsyncSession, arguments: dict[str, Any]) -> dict[str, Any]:
    connection_name = arguments.get("connectionName", "")
    table = arguments.get("table", "")
    schema = arguments.get("schema", "public")

    if not connection_name:
        return {"error": "missing_connectionName", "detail": "'connectionName' is required"}
    if not table:
        return {"error": "missing_table", "detail": "'table' is required"}

    conn, conn_type = await _resolve_connection(db, connection_name)

    pool_manager = DatabasePoolManager.get_instance()
    pool = await pool_manager.get_pool(db, str(conn.id))

    # Columns
    sql = _get_sql(conn_type, "describe_table")
    async with pool.acquire() as c:
        col_rows = await c.fetch(sql, schema, table, timeout=10)

    if not col_rows:
        return {"error": "not_found", "detail": f"Table '{schema}.{table}' not found or has no columns"}

    # Load annotations for this table
    annotations = await _get_annotations(db, str(conn.id), schema, table)

    # Table-level annotation
    table_ann = annotations.get(f"table:{schema}.{table}")

    columns = []
    for row in col_rows:
        col: dict[str, Any] = {
            "name": row["column_name"],
            "type": row["data_type"],
            "udt": row["udt_name"],
            "nullable": row["is_nullable"] == "YES",
        }
        if row["column_default"]:
            col["default"] = row["column_default"]
        if row["character_maximum_length"]:
            col["max_length"] = row["character_maximum_length"]
        if row["numeric_precision"]:
            col["precision"] = row["numeric_precision"]
            if row["numeric_scale"]:
                col["scale"] = row["numeric_scale"]

        # Column annotation
        col_ann = annotations.get(f"column:{schema}.{table}.{row['column_name']}")
        if col_ann:
            col["annotation"] = col_ann

        columns.append(col)

    # Indexes
    idx_sql = _get_sql(conn_type, "list_indexes")
    async with pool.acquire() as c:
        idx_rows = await c.fetch(idx_sql, schema, table, timeout=10)

    indexes: dict[str, dict[str, Any]] = {}
    for row in idx_rows:
        idx_name = row["index_name"]
        if idx_name not in indexes:
            indexes[idx_name] = {
                "name": idx_name,
                "columns": [],
                "unique": row["is_unique"],
                "primary": row["is_primary"],
            }
        indexes[idx_name]["columns"].append(row["column_name"])

    # Foreign keys
    fk_sql = _get_sql(conn_type, "foreign_keys")
    async with pool.acquire() as c:
        fk_rows = await c.fetch(fk_sql, schema, table, timeout=10)

    foreign_keys = []
    for row in fk_rows:
        foreign_keys.append({
            "column": row["column_name"],
            "references": f"{row['foreign_table_schema']}.{row['foreign_table_name']}.{row['foreign_column_name']}",
            "constraint": row["constraint_name"],
        })

    result: dict[str, Any] = {
        "connectionName": connection_name,
        "schema": schema,
        "table": table,
        "columns": columns,
        "indexes": list(indexes.values()),
        "foreign_keys": foreign_keys,
    }
    if table_ann:
        result["annotation"] = table_ann

    return result
