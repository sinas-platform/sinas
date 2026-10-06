"""Templates applier."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from app.models.template import Template
from app.schemas.spec.template import TemplateSpec
from app.services.resources.base import ApplyContext, ResourceApplier


class TemplateApplier(ResourceApplier[TemplateSpec]):
    kind = "templates"
    label = "Template"
    noun = "template"
    config_section = "templates"
    spec_model = TemplateSpec
    model = Template
    keep_unless_declared = ("is_active",)

    def key_of(self, spec: TemplateSpec) -> str:
        return spec.key

    def key_of_row(self, row: Template) -> str:
        return f"{row.namespace}/{row.name}"

    def config_key(self, item: Any) -> str:
        return f"{item.namespace}/{item.name}"

    async def find(self, ctx: ApplyContext, key: str) -> Template | None:
        namespace, name = key.split("/", 1)
        return (
            await ctx.db.execute(
                select(Template)
                .where(Template.namespace == namespace, Template.name == name)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    def spec_from_row(self, row: Template) -> TemplateSpec:
        return TemplateSpec.model_construct(
            namespace=row.namespace,
            name=row.name,
            description=row.description or None,
            title=row.title or None,
            html_content=row.html_content,
            text_content=row.text_content or None,
            variable_schema=dict(row.variable_schema or {}),
            is_active=row.is_active is not False,
        )

    def new_row(self, spec: TemplateSpec, ctx: ApplyContext) -> Template:
        owner = uuid.UUID(str(ctx.owner_user_id))
        return Template(user_id=owner, created_by=owner)

    async def write_row(self, row: Template, spec: TemplateSpec, ctx: ApplyContext) -> None:
        row.namespace = spec.namespace
        row.name = spec.name
        row.description = spec.description
        row.title = spec.title
        row.html_content = spec.html_content
        row.text_content = spec.text_content
        row.variable_schema = dict(spec.variable_schema)
        row.is_active = spec.is_active
        editor = ctx.actor_user_id or ctx.owner_user_id
        if editor:
            row.updated_by = uuid.UUID(str(editor))
