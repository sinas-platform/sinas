"""
Data source appliers: LLM providers, database connections, annotations
"""
import logging
import uuid as uuid_lib
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import encryption_service
from app.models.database_connection import DatabaseConnection
from app.models.table_annotation import TableAnnotation
from app.schemas.config import OwnershipSkip

logger = logging.getLogger(__name__)



def _secret_changed(stored: Optional[str], declared: Optional[str]) -> bool:
    """Whether the config declares a secret that differs from the stored one.

    Secrets are deliberately left out of the change checksum (it is stored in
    plain sight), so a rotated key alone used to look "unchanged" and was
    silently ignored. Not declaring one keeps the stored value, as before."""
    if not declared:
        return False
    if not stored:
        return True
    try:
        return encryption_service.decrypt(stored) != declared
    except Exception:
        return True  # unreadable (e.g. a rotated encryption key): write it fresh

async def apply_database_connections(
    db: AsyncSession,
    connections: list,
    dry_run: bool,
    managed_by: str,
    config_name: str,
    calculate_hash: Any,
    track_change: Any,
    errors: list[str],
    warnings: list[str],
    database_connection_ids: dict[str, str],
) -> None:
    """Apply database connection configurations"""
    for conn_config in connections:
        try:
            stmt = select(DatabaseConnection).where(
                DatabaseConnection.name == conn_config.name
            )
            result = await db.execute(stmt)
            existing = result.scalar_one_or_none()

            # Don't include password in hash (it's encrypted)
            config_hash = calculate_hash(
                {
                    "name": conn_config.name,
                    "connection_type": conn_config.connectionType,
                    "host": conn_config.host,
                    "port": conn_config.port,
                    "database": conn_config.database,
                    "username": conn_config.username,
                    "ssl_mode": conn_config.sslMode,
                    "config": conn_config.config,
                }
            )

            if existing:
                if existing.managed_by != managed_by:
                    warnings.append(
                        OwnershipSkip(f"Database connection '{conn_config.name}' exists but is not managed by '{managed_by}'. Skipping.")
                    )
                    track_change(
                        "unchanged", "databaseConnections", conn_config.name
                    )
                    database_connection_ids[conn_config.name] = str(existing.id)
                    continue

                if existing.config_checksum == config_hash and not _secret_changed(
                    existing.password, conn_config.password
                ):
                    track_change(
                        "unchanged", "databaseConnections", conn_config.name
                    )
                    database_connection_ids[conn_config.name] = str(existing.id)
                    continue

                if not dry_run:
                    existing.connection_type = conn_config.connectionType
                    existing.host = conn_config.host
                    existing.port = conn_config.port
                    existing.database = conn_config.database
                    existing.username = conn_config.username
                    existing.ssl_mode = conn_config.sslMode
                    existing.config = conn_config.config
                    if conn_config.password:
                        existing.password = encryption_service.encrypt(conn_config.password)
                    existing.config_checksum = config_hash
                    existing.updated_at = datetime.utcnow()

                track_change("update", "databaseConnections", conn_config.name)
                database_connection_ids[conn_config.name] = str(existing.id)

            else:
                if not dry_run:
                    encrypted_password = None
                    if conn_config.password:
                        encrypted_password = encryption_service.encrypt(
                            conn_config.password
                        )

                    new_conn = DatabaseConnection(
                        name=conn_config.name,
                        connection_type=conn_config.connectionType,
                        host=conn_config.host,
                        port=conn_config.port,
                        database=conn_config.database,
                        username=conn_config.username,
                        password=encrypted_password,
                        ssl_mode=conn_config.sslMode,
                        config=conn_config.config,
                        is_active=True,
                        managed_by=managed_by,
                        config_name=config_name,
                        config_checksum=config_hash,
                    )
                    db.add(new_conn)
                    await db.flush()
                    database_connection_ids[conn_config.name] = str(new_conn.id)
                else:
                    database_connection_ids[conn_config.name] = "dry-run-id"

                track_change("create", "databaseConnections", conn_config.name)

        except Exception as e:
            errors.append(
                f"Error applying database connection '{conn_config.name}': {str(e)}"
            )

    # Apply annotations for all connections
    for conn_config in connections:
        if conn_config.annotations:
            conn_id = database_connection_ids.get(conn_config.name)
            if conn_id and conn_id != "dry-run-id":
                await apply_annotations(
                    db, conn_config.name, conn_id, conn_config.annotations,
                    dry_run, track_change, errors,
                )
            elif dry_run:
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
