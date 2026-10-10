"""Database connections applier.

Admin-managed and owner-less. Deleting one through the API switches it off
(queries and triggers point at it by id); config keeps that state unless it
declares isActive. Packages never declare connections.

The built-in connection (managed_by "system") belongs to the platform: it
follows the deployment's own database settings and is found by its name on
every boot. Config and packages leave it alone, and an API edit (of the
fields the API lets change) doesn't detach it.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import select

from app.core.encryption import encryption_service
from app.models.database_connection import DatabaseConnection
from app.schemas.spec.database_connection import DatabaseConnectionSpec
from app.services.resources.base import (
    ApplyContext,
    OwnershipDecision,
    ResourceApplier,
    ownership_decision,
)

logger = logging.getLogger(__name__)

SYSTEM = "system"


class DatabaseConnectionApplier(ResourceApplier[DatabaseConnectionSpec]):
    kind = "databaseConnections"
    label = "Database connection"
    noun = "database connection"
    config_section = "databaseConnections"
    spec_model = DatabaseConnectionSpec
    model = DatabaseConnection
    keep_unless_declared = ("password", "is_active", "read_only")
    deleted_with_package = False

    def key_of(self, spec: DatabaseConnectionSpec) -> str:
        return spec.key

    def key_of_row(self, row: DatabaseConnection) -> str:
        return row.name

    def config_key(self, item: Any) -> str:
        return item.name

    async def find(self, ctx: ApplyContext, key: str) -> DatabaseConnection | None:
        return (
            await ctx.db.execute(
                select(DatabaseConnection)
                .where(DatabaseConnection.name == key)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    def spec_from_row(self, row: DatabaseConnection) -> DatabaseConnectionSpec:
        password: Optional[str] = None
        if row.password:
            try:
                password = encryption_service.decrypt(row.password)
            except Exception:  # unreadable (key changed): treated as unknown
                logger.warning("Password of database connection '%s' could not be decrypted", row.name)
        return DatabaseConnectionSpec.model_construct(
            name=row.name,
            connection_type=row.connection_type,
            host=row.host,
            port=row.port,
            database=row.database,
            username=row.username,
            password=password,
            ssl_mode=row.ssl_mode or None,
            config=dict(row.config or {}),
            read_only=bool(row.read_only),
            is_active=row.is_active is not False,
        )

    def new_row(self, spec: DatabaseConnectionSpec, ctx: ApplyContext) -> DatabaseConnection:
        return DatabaseConnection()

    def write_fields(self, row: DatabaseConnection, spec: DatabaseConnectionSpec) -> None:
        row.name = spec.name
        row.connection_type = spec.connection_type
        row.host = spec.host
        row.port = spec.port
        row.database = spec.database
        row.username = spec.username
        # Write-only, and never cleared by leaving it out.
        if spec.password is not None:
            row.password = encryption_service.encrypt(spec.password)
        row.ssl_mode = spec.ssl_mode
        row.config = dict(spec.config)
        row.read_only = spec.read_only
        row.is_active = spec.is_active

    def ownership(self, row: DatabaseConnection, ctx: ApplyContext) -> OwnershipDecision:
        if row.managed_by == SYSTEM:
            # The API may change what it lets change (the endpoint locks the
            # rest), without detaching it from the platform.
            return "write" if ctx.origin == "api" else "skip"
        return ownership_decision(row.managed_by, ctx, row.config_name)

    def readable_password(self, row: DatabaseConnection) -> Optional[str]:
        """For an export that must carry it: unreadable is an error."""
        if not row.password:
            return None
        try:
            return encryption_service.decrypt(row.password)
        except Exception as e:
            raise ValueError(
                f"The password of database connection '{row.name}' can't be decrypted "
                "(was ENCRYPTION_KEY changed?); export it without secrets or set it again"
            ) from e

    def secret_values(self, spec: DatabaseConnectionSpec) -> dict[str, Any]:
        return {"password": spec.password} if spec.password is not None else {}

    def with_secrets(self, state: dict[str, Any], secrets: dict[str, Any]) -> dict[str, Any]:
        return {**state, "password": secrets["password"]} if "password" in secrets else state
