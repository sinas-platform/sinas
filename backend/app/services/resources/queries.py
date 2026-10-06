"""Queries applier."""

from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy import select

from app.models.database_connection import DatabaseConnection
from app.models.query import Query
from app.schemas.spec.query import QuerySpec
from app.services.resources.base import ApplyContext, ReferenceNotFound, ResourceApplier


class QueryApplier(ResourceApplier[QuerySpec]):
    kind = "queries"
    label = "Query"
    noun = "query"
    config_section = "queries"
    spec_model = QuerySpec
    model = Query
    reference_fields = ("connection_name",)
    keep_unless_declared = ("is_active",)

    def key_of(self, spec: QuerySpec) -> str:
        return spec.key

    def key_of_row(self, row: Query) -> str:
        return f"{row.namespace}/{row.name}"

    def config_key(self, item: Any) -> str:
        return f"{item.namespace}/{item.name}"

    async def find(self, ctx: ApplyContext, key: str) -> Query | None:
        namespace, name = key.split("/", 1)
        return (
            await ctx.db.execute(
                select(Query)
                .where(Query.namespace == namespace, Query.name == name)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    def spec_from_row(self, row: Query, connection_name: Optional[str] = None) -> QuerySpec:
        return QuerySpec.model_construct(
            namespace=row.namespace,
            name=row.name,
            description=row.description or None,
            connection_name=connection_name,
            operation=row.operation,
            sql=row.sql,
            input_schema=dict(row.input_schema or {}),
            output_schema=dict(row.output_schema or {}),
            timeout_ms=row.timeout_ms,
            max_rows=row.max_rows,
            is_active=row.is_active is not False,
        )

    async def current_spec(self, ctx: ApplyContext, row: Query) -> QuerySpec:
        return self.spec_from_row(row, await connection_name(ctx.db, row.database_connection_id))

    def new_row(self, spec: QuerySpec, ctx: ApplyContext) -> Query:
        return Query(user_id=uuid.UUID(str(ctx.owner_user_id)))

    async def _connection_id(self, ctx: ApplyContext, name: str) -> uuid.UUID:
        connection_id = (
            await ctx.db.execute(
                select(DatabaseConnection.id).where(DatabaseConnection.name == name)
            )
        ).scalar_one_or_none()
        if connection_id is None:
            raise ReferenceNotFound(f"Database connection '{name}' not found")
        return connection_id

    async def write_row(self, row: Query, spec: QuerySpec, ctx: ApplyContext) -> None:
        # The connection is held by id; re-resolve the name only when the spec
        # names another connection. Re-resolving on every edit could follow
        # the name to a different database after a rename.
        current = await connection_name(ctx.db, row.database_connection_id)
        if current is None or current != spec.connection_name:
            row.database_connection_id = await self._connection_id(ctx, spec.connection_name)
        row.namespace = spec.namespace
        row.name = spec.name
        row.description = spec.description
        row.operation = spec.operation
        row.sql = spec.sql
        row.input_schema = dict(spec.input_schema)
        row.output_schema = dict(spec.output_schema)
        row.timeout_ms = spec.timeout_ms
        row.max_rows = spec.max_rows
        row.is_active = spec.is_active

    async def check_references(self, spec: QuerySpec, ctx: ApplyContext) -> None:
        # A preview accepts a connection the same config declares.
        if not ctx.declared("databaseConnections", spec.connection_name):
            await self._connection_id(ctx, spec.connection_name)


async def connection_name(db, connection_id) -> Optional[str]:
    if connection_id is None:
        return None
    return (
        await db.execute(
            select(DatabaseConnection.name).where(DatabaseConnection.id == connection_id)
        )
    ).scalar_one_or_none()
