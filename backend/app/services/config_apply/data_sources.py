"""
Data source appliers: LLM providers, database connections, annotations
"""
import logging
import uuid as uuid_lib
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database_connection import DatabaseConnection
from app.models.table_annotation import TableAnnotation

logger = logging.getLogger(__name__)



async def apply_connection_annotations(
    db: AsyncSession, connections: list, dry_run: bool, track_change: Any, errors: list[str]
) -> None:
    """Apply the declared table/column annotations of each connection (after
    the connections themselves). Additive: config never removes one."""
    for conn_config in connections:
        if not conn_config.annotations:
            continue
        conn_id = (
            await db.execute(
                select(DatabaseConnection.id).where(DatabaseConnection.name == conn_config.name)
            )
        ).scalar_one_or_none()
        if conn_id is not None:
            await apply_annotations(
                db, conn_config.name, str(conn_id), conn_config.annotations,
                dry_run, track_change, errors,
            )
        elif dry_run:  # the connection is only declared so far
            for ann in conn_config.annotations:
                target = f"{conn_config.name}/{ann.schemaName}.{ann.tableName}"
                if ann.columnName:
                    target += f".{ann.columnName}"
                track_change("create", "annotations", target)


async def apply_annotations(
    db: AsyncSession,
    connection_name: str,
    connection_id: str,
    annotations: list,
    dry_run: bool,
    track_change: Any,
    errors: list[str],
) -> None:
    """Apply table/column annotations for a database connection."""
    conn_uuid = uuid_lib.UUID(connection_id)

    for ann in annotations:
        target = f"{connection_name}/{ann.schemaName}.{ann.tableName}"
        if ann.columnName:
            target += f".{ann.columnName}"
        try:
            q = select(TableAnnotation).where(
                TableAnnotation.database_connection_id == conn_uuid,
                TableAnnotation.schema_name == ann.schemaName,
                TableAnnotation.table_name == ann.tableName,
            )
            if ann.columnName is not None:
                q = q.where(TableAnnotation.column_name == ann.columnName)
            else:
                q = q.where(TableAnnotation.column_name.is_(None))

            result = await db.execute(q)
            existing = result.scalar_one_or_none()

            if existing:
                changed = False
                if ann.displayName is not None and existing.display_name != ann.displayName:
                    if not dry_run:
                        existing.display_name = ann.displayName
                    changed = True
                if ann.description is not None and existing.description != ann.description:
                    if not dry_run:
                        existing.description = ann.description
                    changed = True

                track_change("update" if changed else "unchanged", "annotations", target)
            else:
                if not dry_run:
                    db.add(TableAnnotation(
                        database_connection_id=conn_uuid,
                        schema_name=ann.schemaName,
                        table_name=ann.tableName,
                        column_name=ann.columnName,
                        display_name=ann.displayName,
                        description=ann.description,
                    ))
                track_change("create", "annotations", target)

        except Exception as e:
            errors.append(f"Error applying annotation '{target}': {str(e)}")
