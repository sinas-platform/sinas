"""Database triggers (CDC) applier."""

from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy import and_, case, or_, select

from app.models.database_connection import DatabaseConnection
from app.models.database_trigger import DatabaseTrigger
from app.models.function import Function
from app.schemas.spec.database_trigger import DatabaseTriggerSpec
from app.services.resources.base import (
    ApplyContext,
    CdcTriggerChanged,
    ReferenceNotFound,
    ResourceApplier,
)

# Changing any of these points the poll bookmark at different data: the old
# value would be compared against (and cast to the type of) another column.
_BOOKMARK_FIELDS = ("database_connection_id", "schema_name", "table_name", "poll_column")


class DatabaseTriggerApplier(ResourceApplier[DatabaseTriggerSpec]):
    kind = "databaseTriggers"
    label = "Database trigger"
    noun = "database trigger"
    config_section = "databaseTriggers"
    spec_model = DatabaseTriggerSpec
    model = DatabaseTrigger
    reference_fields = ("connection_name", "target_type", "target_namespace", "target_name")

    def key_of(self, spec: DatabaseTriggerSpec) -> str:
        return spec.name

    def key_of_row(self, row: DatabaseTrigger) -> str:
        return row.name

    def config_key(self, item: Any) -> str:
        return item.name

    async def find(self, ctx: ApplyContext, key: str) -> DatabaseTrigger | None:
        """Names are unique per owner, not globally.

        - A REST write addresses the owner's own trigger.
        - A config or package apply declares *the* trigger by that name,
          whoever applies it: the one this source already manages, else the
          applying user's, else the only other one (ownership_decision then
          adopts, skips or warns, as for every kind). Scoping it to the
          applying user alone created a second trigger on the same table
          whenever another admin (or a reordered boot) applied the same
          config — and every change then ran the target twice.
        """
        owner = uuid.UUID(str(ctx.owner_user_id)) if ctx.owner_user_id else None
        query = select(DatabaseTrigger).where(DatabaseTrigger.name == key)
        if ctx.origin == "api":
            query = query.where(DatabaseTrigger.user_id == owner)
        else:
            same_source = and_(
                DatabaseTrigger.managed_by == ctx.managed_by,
                or_(
                    DatabaseTrigger.config_name == ctx.config_name,
                    DatabaseTrigger.managed_by.like("pkg:%"),
                ),
            )
            query = query.order_by(
                case((same_source, 0), (DatabaseTrigger.user_id == owner, 1), else_=2),
                DatabaseTrigger.created_at,
            ).limit(1)
        return (
            await ctx.db.execute(
                query.with_for_update().execution_options(populate_existing=True)
            )
        ).scalars().first()

    def spec_from_row(
        self, row: DatabaseTrigger, connection_name: Optional[str] = None
    ) -> DatabaseTriggerSpec:
        target_type = row.target_type or "function"
        return DatabaseTriggerSpec.model_construct(
            name=row.name,
            connection_name=connection_name,
            schema_name=row.schema_name,
            table_name=row.table_name,
            operations=list(row.operations or []),
            target_type=target_type,
            target_namespace=getattr(row, f"{target_type}_namespace", None) or "default",
            target_name=getattr(row, f"{target_type}_name", None),
            poll_column=row.poll_column,
            poll_interval_seconds=row.poll_interval_seconds,
            batch_size=row.batch_size,
            is_active=row.is_active is not False,
        )

    async def current_spec(self, ctx: ApplyContext, row: DatabaseTrigger) -> DatabaseTriggerSpec:
        name = (
            await ctx.db.execute(
                select(DatabaseConnection.name).where(
                    DatabaseConnection.id == row.database_connection_id
                )
            )
        ).scalar_one_or_none()
        return self.spec_from_row(row, name)

    def new_row(self, spec: DatabaseTriggerSpec, ctx: ApplyContext) -> DatabaseTrigger:
        return DatabaseTrigger(user_id=uuid.UUID(str(ctx.owner_user_id)))

    async def _connection_id(self, ctx: ApplyContext, name: str) -> uuid.UUID:
        # FOR SHARE: not renamed (and the name reused) before this commits.
        connection_id = (
            await ctx.db.execute(
                select(DatabaseConnection.id)
                .where(DatabaseConnection.name == name)
                .with_for_update(read=True)
            )
        ).scalar_one_or_none()
        if connection_id is None:
            raise ReferenceNotFound(f"Database connection '{name}' not found")
        return connection_id

    async def write_row(
        self, row: DatabaseTrigger, spec: DatabaseTriggerSpec, ctx: ApplyContext
    ) -> None:
        # Held by id; resolve the name only when the spec names another
        # connection, or an edit could follow a reused name to another database.
        from app.services.resources.queries import connection_name

        current = await connection_name(ctx.db, row.database_connection_id)
        if current is not None and current == spec.connection_name:
            connection_id = row.database_connection_id
        else:
            connection_id = await self._connection_id(ctx, spec.connection_name)
        before = {field: getattr(row, field, None) for field in _BOOKMARK_FIELDS}
        row.name = spec.name
        row.database_connection_id = connection_id
        row.schema_name = spec.schema_name
        row.table_name = spec.table_name
        row.operations = list(spec.operations)
        row.target_type = spec.target_type
        function = spec.target_type == "function"
        row.function_namespace = spec.target_namespace if function else "default"  # NOT NULL
        row.function_name = spec.target_name if function else None
        row.pipeline_namespace = None if function else spec.target_namespace
        row.pipeline_name = None if function else spec.target_name
        row.poll_column = spec.poll_column
        row.poll_interval_seconds = spec.poll_interval_seconds
        row.batch_size = spec.batch_size
        row.is_active = spec.is_active
        if row.id is not None and any(
            before[field] != getattr(row, field) for field in _BOOKMARK_FIELDS
        ):
            # Start over on the new source rather than resume from a bookmark
            # that belongs to another column — which failed every poll when
            # the types differed. Runtime state, so not part of the spec.
            row.last_poll_value = None
            row.error_message = None

    async def check_references(self, spec: DatabaseTriggerSpec, ctx: ApplyContext) -> None:
        ns, name, db = spec.target_namespace, spec.target_name, ctx.db
        # A preview accepts what the same config declares — a function only
        # if declared active, as the check below requires.
        if not ctx.declared("databaseConnections", spec.connection_name):
            await self._connection_id(ctx, spec.connection_name)
        function = spec.target_type == "function"
        if ctx.declared("functions" if function else "pipelines", spec.target, active=function):
            return
        if spec.target_type == "function":
            scope = ctx.reference_scope_user_id
            if not await Function.get_by_name(db, ns, name, uuid.UUID(str(scope)) if scope else None):
                raise ReferenceNotFound(f"Function '{ns}/{name}' not found")
        else:
            from app.models.pipeline import Pipeline

            if not await Pipeline.get_by_name(db, ns, name):
                raise ReferenceNotFound(f"Pipeline '{ns}/{name}' not found")

    def effects(self, action: str, row: DatabaseTrigger) -> list[Any]:
        cdc_action = {"create": "add", "update": "update", "delete": "remove"}[action]
        return [CdcTriggerChanged(cdc_action, str(row.id))]
