"""Database schema browser, DDL, data browser, and annotation endpoints.

All endpoints are nested under /database-connections/{name}/...
and use the connection *name* (not UUID) as the path parameter.

Permissions:
  schema:all        — introspection (list schemas/tables/views, get detail) + annotations + non-destructive DDL (create table/view, add column)
  schema_destroy:all — destructive DDL (DROP TABLE, DROP VIEW, DROP COLUMN via alter)
  data:all          — data browser (browse/insert/update/delete rows)

Read-only guard:
  Connections with read_only=True block all DDL and data mutations.
  This does NOT affect Query template execution (queries are trusted).
"""

import json
import logging
import uuid
from typing import Any, Optional

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_permission
from app.core.database import get_db
from app.models.database_connection import DatabaseConnection
from app.models.table_annotation import TableAnnotation
from app.schemas.database_schema import (
    AlterTableRequest,
    AnnotationItem,
    AnnotationsUpsertRequest,
    BrowseRowsResponse,
    ColumnInfo,
    ConstraintInfo,
    CreateTableRequest,
    CreateViewRequest,
    DeleteRowsRequest,
    FilterCondition,
    IndexInfo,
    InsertRowsRequest,
    SchemaInfo,
    TableDetail,
    TableInfo,
    UpdateRowsRequest,
    ViewInfo,
)
from app.services.database import get_schema_service
from app.services.database_pool import DatabasePoolManager

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Shared helpers ─────────────────────────────────────────────────


async def _get_connection_and_pool(
    name: str, db: AsyncSession
) -> tuple[DatabaseConnection, asyncpg.Pool]:
    """Resolve a connection by name and return (connection, asyncpg_pool)."""
    conn = await DatabaseConnection.get_by_name(db, name)
    if not conn:
        raise HTTPException(status_code=404, detail=f"Database connection '{name}' not found")
    pool_mgr = DatabasePoolManager.get_instance()
    pool = await pool_mgr.get_pool(db, str(conn.id))
    return conn, pool


def _assert_writable(conn: DatabaseConnection) -> None:
    """Raise 403 if connection is marked read-only."""
    if conn.read_only:
        raise HTTPException(
            status_code=403,
            detail=f"Connection '{conn.name}' is read-only. Disable read-only in connection settings to allow modifications through the Schema Browser.",
        )


def _pg_error_response(exc: Exception) -> HTTPException:
    """Wrap asyncpg / postgres errors as 400 responses."""
    msg = str(exc)
    if hasattr(exc, "message"):
        msg = exc.message  # type: ignore[union-attr]
    return HTTPException(status_code=400, detail=msg)


async def _load_annotations(
    db: AsyncSession, connection_id: str, schema_name: str
) -> dict[tuple[str, Optional[str]], TableAnnotation]:
    """Load annotations keyed by (table_name, column_name|None)."""
    result = await db.execute(
        select(TableAnnotation).where(
            TableAnnotation.database_connection_id == connection_id,
            TableAnnotation.schema_name == schema_name,
        )
    )
    annotations = result.scalars().all()
    return {(a.table_name, a.column_name): a for a in annotations}


# ── Introspection (schema:all) ─────────────────────────────────────


@router.get(
    "/{name}/schemas",
    response_model=list[SchemaInfo],
    summary="List database schemas",
)
async def list_schemas(
    name: str,
    user_id: str = Depends(require_permission("sinas.database_connections.schema:all")),
    db: AsyncSession = Depends(get_db),
):
    conn, pool = await _get_connection_and_pool(name, db)
    svc = get_schema_service(conn.connection_type)
    try:
        schemas = await svc.list_schemas(pool)
    except Exception as e:
        raise _pg_error_response(e)
    return [SchemaInfo(**s) for s in schemas]


@router.get(
    "/{name}/tables",
    response_model=list[TableInfo],
    summary="List tables in a schema",
)
async def list_tables(
    name: str,
    schema: str = Query("public"),
    user_id: str = Depends(require_permission("sinas.database_connections.schema:all")),
    db: AsyncSession = Depends(get_db),
):
    conn, pool = await _get_connection_and_pool(name, db)
    svc = get_schema_service(conn.connection_type)
    try:
        tables = await svc.list_tables(pool, schema=schema)
    except Exception as e:
        raise _pg_error_response(e)

    # Merge annotations
    ann_map = await _load_annotations(db, str(conn.id), schema)
    result = []
    for t in tables:
        ann = ann_map.get((t["table_name"], None))
        result.append(
            TableInfo(
                **t,
                display_name=ann.display_name if ann else None,
                description=ann.description if ann else None,
            )
        )
    return result


@router.get(
    "/{name}/tables/{table}",
    response_model=TableDetail,
    summary="Get table detail (columns, constraints, indexes)",
)
async def get_table_detail(
    name: str,
    table: str,
    schema: str = Query("public"),
    user_id: str = Depends(require_permission("sinas.database_connections.schema:all")),
    db: AsyncSession = Depends(get_db),
):
    conn, pool = await _get_connection_and_pool(name, db)
    svc = get_schema_service(conn.connection_type)
    try:
        detail = await svc.get_table_detail(pool, table, schema=schema)
    except Exception as e:
        raise _pg_error_response(e)

    if not detail["columns"]:
        raise HTTPException(status_code=404, detail=f"Table '{schema}.{table}' not found")

    # Merge annotations
    ann_map = await _load_annotations(db, str(conn.id), schema)
    table_ann = ann_map.get((table, None))

    columns = []
    for c in detail["columns"]:
        col_ann = ann_map.get((table, c["column_name"]))
        columns.append(
            ColumnInfo(
                **c,
                display_name=col_ann.display_name if col_ann else None,
                description=col_ann.description if col_ann else None,
            )
        )

    return TableDetail(
        table_name=detail["table_name"],
        schema_name=detail["schema_name"],
        columns=columns,
        constraints=[ConstraintInfo(**c) for c in detail["constraints"]],
        indexes=[IndexInfo(**i) for i in detail["indexes"]],
        display_name=table_ann.display_name if table_ann else None,
        description=table_ann.description if table_ann else None,
    )


@router.get(
    "/{name}/views",
    response_model=list[ViewInfo],
    summary="List views in a schema",
)
async def list_views(
    name: str,
    schema: str = Query("public"),
    user_id: str = Depends(require_permission("sinas.database_connections.schema:all")),
    db: AsyncSession = Depends(get_db),
):
    conn, pool = await _get_connection_and_pool(name, db)
    svc = get_schema_service(conn.connection_type)
    try:
        views = await svc.list_views(pool, schema=schema)
    except Exception as e:
        raise _pg_error_response(e)
    return [ViewInfo(**v) for v in views]


# ── Non-destructive DDL (schema:all + writable) ────────────────────


@router.post(
    "/{name}/tables",
    status_code=status.HTTP_201_CREATED,
    summary="Create a table",
)
async def create_table(
    name: str,
    request: CreateTableRequest,
    user_id: str = Depends(require_permission("sinas.database_connections.schema:all")),
    db: AsyncSession = Depends(get_db),
):
    conn, pool = await _get_connection_and_pool(name, db)
    _assert_writable(conn)
    svc = get_schema_service(conn.connection_type)
    columns = [c.model_dump() for c in request.columns]
    try:
        await svc.create_table(
            pool,
            request.table_name,
            columns,
            schema=request.schema_name,
            if_not_exists=request.if_not_exists,
        )
    except Exception as e:
        raise _pg_error_response(e)
    return {"status": "created", "table": f"{request.schema_name}.{request.table_name}"}


@router.post(
    "/{name}/views",
    status_code=status.HTTP_201_CREATED,
    summary="Create a view",
)
async def create_view(
    name: str,
    request: CreateViewRequest,
    user_id: str = Depends(require_permission("sinas.database_connections.schema:all")),
    db: AsyncSession = Depends(get_db),
):
    conn, pool = await _get_connection_and_pool(name, db)
    _assert_writable(conn)
    svc = get_schema_service(conn.connection_type)
    try:
        await svc.create_view(
            pool,
            request.name,
            request.sql,
            schema=request.schema_name,
            or_replace=request.or_replace,
        )
    except Exception as e:
        raise _pg_error_response(e)
    return {"status": "created", "view": f"{request.schema_name}.{request.name}"}


@router.patch(
    "/{name}/tables/{table}",
    summary="Alter a table (add/drop/rename columns)",
)
async def alter_table(
    name: str,
    table: str,
    request: AlterTableRequest,
    user_id: str = Depends(require_permission("sinas.database_connections.schema:all")),
    db: AsyncSession = Depends(get_db),
):
    conn, pool = await _get_connection_and_pool(name, db)
    _assert_writable(conn)

    # Drop columns is destructive — require destroy permission
    if request.drop_columns:
        from app.core.permissions import check_permission

        # Need to load permissions for this user
        from app.core.auth import get_user_permissions

        permissions = await get_user_permissions(db, user_id)
        if not check_permission(permissions, "sinas.database_connections.schema_destroy:all"):
            raise HTTPException(
                status_code=403,
                detail="Dropping columns requires sinas.database_connections.schema_destroy:all permission",
            )

    svc = get_schema_service(conn.connection_type)
    try:
        await svc.alter_table(
            pool,
            table,
            schema=request.schema_name,
            add_columns=[c.model_dump() for c in request.add_columns] if request.add_columns else None,
            drop_columns=request.drop_columns,
            rename_columns=request.rename_columns,
        )
    except Exception as e:
        raise _pg_error_response(e)
    return {"status": "altered", "table": f"{request.schema_name}.{table}"}


# ── Destructive DDL (schema_destroy:all + writable) ────────────────


@router.delete(
    "/{name}/tables/{table}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Drop a table",
)
async def drop_table(
    name: str,
    table: str,
    schema: str = Query("public"),
    cascade: bool = Query(False),
    user_id: str = Depends(require_permission("sinas.database_connections.schema_destroy:all")),
    db: AsyncSession = Depends(get_db),
):
    conn, pool = await _get_connection_and_pool(name, db)
    _assert_writable(conn)
    svc = get_schema_service(conn.connection_type)
    try:
        await svc.drop_table(pool, table, schema=schema, cascade=cascade)
    except Exception as e:
        raise _pg_error_response(e)


@router.delete(
    "/{name}/views/{view}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Drop a view",
)
async def drop_view(
    name: str,
    view: str,
    schema: str = Query("public"),
    cascade: bool = Query(False),
    user_id: str = Depends(require_permission("sinas.database_connections.schema_destroy:all")),
    db: AsyncSession = Depends(get_db),
):
    conn, pool = await _get_connection_and_pool(name, db)
    _assert_writable(conn)
    svc = get_schema_service(conn.connection_type)
    try:
        await svc.drop_view(pool, view, schema=schema, cascade=cascade)
    except Exception as e:
        raise _pg_error_response(e)


# ── Data Browser ───────────────────────────────────────────────────


@router.get(
    "/{name}/tables/{table}/rows",
    response_model=BrowseRowsResponse,
    summary="Browse table rows",
)
async def browse_rows(
    name: str,
    table: str,
    schema: str = Query("public"),
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    sort_by: Optional[str] = Query(None),
    sort_order: str = Query("asc", pattern="^(asc|desc)$"),
    filters: Optional[str] = Query(None, description="JSON array of {column, operator, value}"),
    user_id: str = Depends(require_permission("sinas.database_connections.data:all")),
    db: AsyncSession = Depends(get_db),
):
    conn, pool = await _get_connection_and_pool(name, db)
    svc = get_schema_service(conn.connection_type)

    parsed_filters = None
    if filters:
        try:
            parsed_filters = json.loads(filters)
            # Validate each filter
            for f in parsed_filters:
                FilterCondition(**f)
        except (json.JSONDecodeError, Exception) as e:
            raise HTTPException(status_code=400, detail=f"Invalid filters: {e}")

    try:
        result = await svc.browse_rows(
            pool,
            table,
            schema=schema,
            filters=parsed_filters,
            sort_by=sort_by,
            sort_order=sort_order,
            limit=limit,
            offset=offset,
        )
    except Exception as e:
        raise _pg_error_response(e)
    return BrowseRowsResponse(**result)


@router.post(
    "/{name}/tables/{table}/rows",
    status_code=status.HTTP_201_CREATED,
    summary="Insert rows",
)
async def insert_rows(
    name: str,
    table: str,
    request: InsertRowsRequest,
    schema: str = Query("public"),
    user_id: str = Depends(require_permission("sinas.database_connections.data:all")),
    db: AsyncSession = Depends(get_db),
):
    conn, pool = await _get_connection_and_pool(name, db)
    _assert_writable(conn)
    svc = get_schema_service(conn.connection_type)
    try:
        inserted = await svc.insert_rows(pool, table, request.rows, schema=schema)
    except Exception as e:
        raise _pg_error_response(e)
    return {"inserted": inserted, "count": len(inserted)}


@router.patch(
    "/{name}/tables/{table}/rows",
    summary="Update rows",
)
async def update_rows(
    name: str,
    table: str,
    request: UpdateRowsRequest,
    schema: str = Query("public"),
    user_id: str = Depends(require_permission("sinas.database_connections.data:all")),
    db: AsyncSession = Depends(get_db),
):
    conn, pool = await _get_connection_and_pool(name, db)
    _assert_writable(conn)
    svc = get_schema_service(conn.connection_type)
    try:
        affected = await svc.update_rows(
            pool, table, where=request.where, set_values=request.set_values, schema=schema
        )
    except Exception as e:
        raise _pg_error_response(e)
    return {"affected_rows": affected}


@router.delete(
    "/{name}/tables/{table}/rows",
    summary="Delete rows",
)
async def delete_rows(
    name: str,
    table: str,
    request: DeleteRowsRequest,
    schema: str = Query("public"),
    user_id: str = Depends(require_permission("sinas.database_connections.data:all")),
    db: AsyncSession = Depends(get_db),
):
    conn, pool = await _get_connection_and_pool(name, db)
    _assert_writable(conn)
    svc = get_schema_service(conn.connection_type)
    try:
        affected = await svc.delete_rows(pool, table, where=request.where, schema=schema)
    except Exception as e:
        raise _pg_error_response(e)
    return {"affected_rows": affected}


# ── Annotations ────────────────────────────────────────────────────


@router.get(
    "/{name}/annotations",
    response_model=list[AnnotationItem],
    summary="Get all annotations for a connection",
)
async def get_annotations(
    name: str,
    user_id: str = Depends(require_permission("sinas.database_connections.schema:all")),
    db: AsyncSession = Depends(get_db),
):
    conn = await DatabaseConnection.get_by_name(db, name)
    if not conn:
        raise HTTPException(status_code=404, detail=f"Database connection '{name}' not found")

    result = await db.execute(
        select(TableAnnotation)
        .where(TableAnnotation.database_connection_id == conn.id)
        .order_by(TableAnnotation.schema_name, TableAnnotation.table_name, TableAnnotation.column_name)
    )
    annotations = result.scalars().all()
    return [
        AnnotationItem(
            schema_name=a.schema_name,
            table_name=a.table_name,
            column_name=a.column_name,
            display_name=a.display_name,
            description=a.description,
        )
        for a in annotations
    ]


@router.put(
    "/{name}/annotations",
    summary="Upsert annotations",
)
async def upsert_annotations(
    name: str,
    request: AnnotationsUpsertRequest,
    user_id: str = Depends(require_permission("sinas.database_connections.schema:all")),
    db: AsyncSession = Depends(get_db),
):
    conn = await DatabaseConnection.get_by_name(db, name)
    if not conn:
        raise HTTPException(status_code=404, detail=f"Database connection '{name}' not found")

    upserted = 0
    for item in request.annotations:
        # Try to find existing
        q = select(TableAnnotation).where(
            TableAnnotation.database_connection_id == conn.id,
            TableAnnotation.schema_name == item.schema_name,
            TableAnnotation.table_name == item.table_name,
        )
        if item.column_name is not None:
            q = q.where(TableAnnotation.column_name == item.column_name)
        else:
            q = q.where(TableAnnotation.column_name.is_(None))

        result = await db.execute(q)
        existing = result.scalar_one_or_none()

        if existing:
            if item.display_name is not None:
                existing.display_name = item.display_name
            if item.description is not None:
                existing.description = item.description
        else:
            db.add(
                TableAnnotation(
                    database_connection_id=conn.id,
                    schema_name=item.schema_name,
                    table_name=item.table_name,
                    column_name=item.column_name,
                    display_name=item.display_name,
                    description=item.description,
                )
            )
        upserted += 1

    await db.commit()
    return {"upserted": upserted}
