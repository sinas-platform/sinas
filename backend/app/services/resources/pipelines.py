"""Pipelines applier."""

from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy import select

from app.models.pipeline import Pipeline
from app.schemas.spec.pipeline import PipelineSpec
from app.services.resources.base import ApplyContext, ResourceApplier


class PipelineApplier(ResourceApplier[PipelineSpec]):
    kind = "pipelines"
    label = "Pipeline"
    noun = "pipeline"
    config_section = "pipelines"
    spec_model = PipelineSpec
    model = Pipeline
    # Operator state, and the pipeline's own: one switched off by hand or
    # auto-disabled after failures stays off unless config says isActive.
    keep_unless_declared = ("is_active",)

    def key_of(self, spec: PipelineSpec) -> str:
        return spec.key

    def key_of_row(self, row: Pipeline) -> str:
        return f"{row.namespace}/{row.name}"

    def config_key(self, item: Any) -> str:
        return f"{item.namespace}/{item.name}"

    async def find(self, ctx: ApplyContext, key: str) -> Pipeline | None:
        namespace, name = key.split("/", 1)
        return (
            await ctx.db.execute(
                select(Pipeline)
                .where(Pipeline.namespace == namespace, Pipeline.name == name)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    def spec_from_row(self, row: Pipeline) -> PipelineSpec:
        return PipelineSpec.model_construct(
            namespace=row.namespace,
            name=row.name,
            description=row.description or None,
            input_schema=dict(row.input_schema or {}),
            steps=list(row.steps or []),
            per_user=dict(row.per_user) if row.per_user is not None else None,
            as_tool=bool(row.as_tool),
            tool_description=row.tool_description or None,
            sync_timeout_seconds=row.sync_timeout_seconds,
            concurrency=row.concurrency,
            disable_after_failures=row.disable_after_failures,
            output_mapping=dict(row.output_mapping) if row.output_mapping is not None else None,
            is_active=row.is_active is not False,
        )

    def new_row(self, spec: PipelineSpec, ctx: ApplyContext) -> Pipeline:
        return Pipeline(user_id=uuid.UUID(str(ctx.owner_user_id)))

    async def write_row(
        self, row: Pipeline, spec: PipelineSpec, ctx: ApplyContext, current: Optional[PipelineSpec] = None
    ) -> None:
        row.namespace = spec.namespace
        row.name = spec.name
        row.description = spec.description
        row.input_schema = dict(spec.input_schema)
        row.steps = list(spec.steps)
        row.per_user = dict(spec.per_user) if spec.per_user is not None else None
        row.as_tool = spec.as_tool
        row.tool_description = spec.tool_description
        row.sync_timeout_seconds = spec.sync_timeout_seconds
        row.concurrency = spec.concurrency
        row.disable_after_failures = spec.disable_after_failures
        row.output_mapping = dict(spec.output_mapping) if spec.output_mapping is not None else None
        if spec.is_active and current is not None and not current.is_active:
            # Switched back on: clear the auto-disable state (the cursor stays).
            row.consecutive_failures = 0
            row.error_message = None
        row.is_active = spec.is_active
