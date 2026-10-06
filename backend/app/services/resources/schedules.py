"""Schedules applier — the design's pilot kind (§5)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import and_, select

from app.models.agent import Agent
from app.models.function import Function
from app.models.schedule import ScheduledJob
from app.schemas.spec.schedule import ScheduleSpec
from app.services.resources.base import (
    ApplyContext,
    ReferenceNotFound,
    ResourceApplier,
    SchedulerJobChanged,
)


class ScheduleApplier(ResourceApplier[ScheduleSpec]):
    kind = "schedules"
    label = "Schedule"
    noun = "schedule"
    config_section = "schedules"
    spec_model = ScheduleSpec
    model = ScheduledJob
    reference_fields = ("schedule_type", "target_namespace", "target_name")

    _FIELDS = (
        "name", "schedule_type", "target_namespace", "target_name", "description",
        "cron_expression", "timezone", "input_data", "content", "is_active",
    )

    def key_of(self, spec: ScheduleSpec) -> str:
        return spec.name

    def key_of_row(self, row: ScheduledJob) -> str:
        return row.name

    def config_key(self, item: Any) -> str:
        return item.name

    async def find(self, ctx: ApplyContext, key: str) -> ScheduledJob | None:
        # Schedule names are globally unique (the column is UNIQUE), so the
        # lookup is global too; who may touch a row is the API layer's call.
        # Locked until commit, like every row an applier may write: two
        # concurrent applies must not both diff against the same old state.
        return (
            await ctx.db.execute(
                select(ScheduledJob)
                .where(ScheduledJob.name == key)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    def spec_from_row(self, row: ScheduledJob) -> ScheduleSpec:
        # No validation: legacy rows written by the config path before it
        # validated (bad cron, agent without content) must still be readable,
        # so they can be fixed, paused or deleted.
        return ScheduleSpec.model_construct(
            **{field: getattr(row, field) for field in self._FIELDS}
        )

    def new_row(self, spec: ScheduleSpec, ctx: ApplyContext) -> ScheduledJob:
        return ScheduledJob(user_id=uuid.UUID(str(ctx.owner_user_id)))

    def write_fields(self, row: ScheduledJob, spec: ScheduleSpec) -> None:
        for field in self._FIELDS:
            setattr(row, field, getattr(spec, field))

    async def check_references(self, spec: ScheduleSpec, ctx: ApplyContext) -> None:
        """Same rules and messages the REST API always had, now on every
        channel. Config apply previously checked nothing here (it relied on a
        parser pre-pass that `force=true` skips), and a REST PATCH that changed
        the target to a pipeline never checked the pipeline at all."""
        ns, name, db = spec.target_namespace, spec.target_name, ctx.db
        # In a preview the same config may be about to create the target.
        # Only in a preview: in a real apply it has been created by now, and
        # the database (not the config) is the truth — if creating it failed,
        # this schedule must fail too. Functions and pipelines must be active,
        # as below; agents need only exist.
        kind = {"function": "functions", "agent": "agents", "pipeline": "pipelines"}
        if ctx.declared(
            kind[spec.schedule_type], spec.target, active=spec.schedule_type != "agent"
        ):
            return
        if spec.schedule_type == "function":
            scope = ctx.reference_scope_user_id
            target = await Function.get_by_name(
                db, ns, name, uuid.UUID(str(scope)) if scope else None
            )
            if not target:
                raise ReferenceNotFound(f"Function '{ns}/{name}' not found")
        elif spec.schedule_type == "pipeline":
            from app.models import Pipeline

            found = (
                await db.execute(
                    select(Pipeline.id).where(
                        and_(
                            Pipeline.namespace == ns,
                            Pipeline.name == name,
                            Pipeline.is_active == True,  # noqa: E712
                        )
                    )
                )
            ).first()
            if not found:
                raise ReferenceNotFound(f"Pipeline '{ns}/{name}' not found or inactive")
        else:
            found = (
                await db.execute(
                    select(Agent.id).where(and_(Agent.namespace == ns, Agent.name == name))
                )
            ).first()
            if not found:
                raise ReferenceNotFound(f"Agent '{ns}/{name}' not found")

    def effects(self, action: str, row: ScheduledJob) -> list[Any]:
        scheduler_action = {"create": "add", "update": "update", "delete": "remove"}[action]
        return [SchedulerJobChanged(scheduler_action, str(row.id))]
