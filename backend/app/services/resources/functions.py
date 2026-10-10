"""Functions applier."""

from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy import func, select

from app.models.function import Function, FunctionVersion
from app.schemas.spec.function import FunctionSpec
from app.services.resources.base import ApplyContext, ResourceApplier

# What a version snapshots; a change to any of these is a new version.
_VERSIONED = ("code", "input_schema", "output_schema")


class FunctionApplier(ResourceApplier[FunctionSpec]):
    kind = "functions"
    label = "Function"
    noun = "function"
    config_section = "functions"
    spec_model = FunctionSpec
    model = Function
    # Operator state a config only changes when it says so. A config left
    # sharedPool/requiresApproval unset to keep them, and switched disabled
    # functions back on (is_active).
    keep_unless_declared = ("is_active", "shared_pool", "requires_approval")

    def key_of(self, spec: FunctionSpec) -> str:
        return spec.key

    def key_of_row(self, row: Function) -> str:
        return f"{row.namespace}/{row.name}"

    def config_key(self, item: Any) -> str:
        return f"{item.namespace}/{item.name}"

    async def find(self, ctx: ApplyContext, key: str) -> Function | None:
        # Disabled ones too (Function.get_by_name skips them): a disabled
        # function still holds its name.
        namespace, name = key.split("/", 1)
        return (
            await ctx.db.execute(
                select(Function)
                .where(Function.namespace == namespace, Function.name == name)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    def spec_from_row(self, row: Function) -> FunctionSpec:
        return FunctionSpec.model_construct(
            namespace=row.namespace,
            name=row.name,
            description=row.description or None,
            code=row.code,
            input_schema=dict(row.input_schema or {}),
            output_schema=dict(row.output_schema or {}),
            icon=row.icon or None,
            timeout=row.timeout,
            shared_pool=bool(row.shared_pool),
            requires_approval=bool(row.requires_approval),
            is_active=row.is_active is not False,
        )

    def new_row(self, spec: FunctionSpec, ctx: ApplyContext) -> Function:
        return Function(user_id=uuid.UUID(str(ctx.owner_user_id)))

    async def write_row(
        self, row: Function, spec: FunctionSpec, ctx: ApplyContext, current: Optional[FunctionSpec] = None
    ) -> None:
        row.namespace = spec.namespace
        row.name = spec.name
        row.description = spec.description
        row.code = spec.code
        row.input_schema = dict(spec.input_schema)
        row.output_schema = dict(spec.output_schema)
        row.icon = spec.icon
        row.timeout = spec.timeout
        row.shared_pool = spec.shared_pool
        row.requires_approval = spec.requires_approval
        row.is_active = spec.is_active

        # A version snapshots code and schemas: v1 on create, the next one
        # whenever they change — on every channel. (REST used to add one
        # whenever code was sent, changed or not; config only on a change.)
        if current is not None and all(getattr(current, f) == getattr(spec, f) for f in _VERSIONED):
            return
        author = ctx.actor_user_id or ctx.owner_user_id or row.user_id
        snapshot = dict(
            code=spec.code,
            input_schema=dict(spec.input_schema),
            output_schema=dict(spec.output_schema),
            created_by=uuid.UUID(str(author)),
        )
        if current is None:
            # Not flushed yet: attach through the relationship (no lazy load
            # on a new object), so the id is filled in at flush.
            row.versions = [FunctionVersion(version=1, **snapshot)]
            return
        latest = (
            await ctx.db.execute(
                select(func.max(FunctionVersion.version)).where(FunctionVersion.function_id == row.id)
            )
        ).scalar()
        ctx.db.add(FunctionVersion(function_id=row.id, version=(latest or 0) + 1, **snapshot))
