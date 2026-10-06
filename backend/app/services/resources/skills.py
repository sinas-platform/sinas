"""Skills applier."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from app.models.skill import Skill
from app.schemas.spec.skill import SkillSpec
from app.services.resources.base import ApplyContext, ResourceApplier


class SkillApplier(ResourceApplier[SkillSpec]):
    kind = "skills"
    label = "Skill"
    noun = "skill"
    config_section = "skills"
    spec_model = SkillSpec
    model = Skill
    # A skill switched off by hand stays off on the next apply.
    keep_unless_declared = ("is_active",)

    def key_of(self, spec: SkillSpec) -> str:
        return spec.key

    def key_of_row(self, row: Skill) -> str:
        return f"{row.namespace}/{row.name}"

    def config_key(self, item: Any) -> str:
        return f"{item.namespace}/{item.name}"

    async def find(self, ctx: ApplyContext, key: str) -> Skill | None:
        namespace, name = key.split("/", 1)
        return (
            await ctx.db.execute(
                select(Skill)
                .where(Skill.namespace == namespace, Skill.name == name)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    def spec_from_row(self, row: Skill) -> SkillSpec:
        return SkillSpec.model_construct(
            namespace=row.namespace,
            name=row.name,
            description=row.description,
            content=row.content,
            is_active=row.is_active is not False,
        )

    def new_row(self, spec: SkillSpec, ctx: ApplyContext) -> Skill:
        return Skill(user_id=uuid.UUID(str(ctx.owner_user_id)))

    def write_fields(self, row: Skill, spec: SkillSpec) -> None:
        row.namespace = spec.namespace
        row.name = spec.name
        row.description = spec.description
        row.content = spec.content
        row.is_active = spec.is_active
