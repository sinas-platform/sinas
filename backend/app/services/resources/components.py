"""Components applier."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from app.models.component import Component
from app.schemas.spec.component import ComponentSpec, EnabledStoreSpec
from app.services.resources.base import ApplyContext, ResourceApplier


class ComponentApplier(ResourceApplier[ComponentSpec]):
    kind = "components"
    label = "Component"
    noun = "component"
    config_section = "components"
    spec_model = ComponentSpec
    model = Component
    # Switched off by hand, it stays off on the next apply unless declared.
    keep_unless_declared = ("is_active",)

    def key_of(self, spec: ComponentSpec) -> str:
        return spec.key

    def key_of_row(self, row: Component) -> str:
        return f"{row.namespace}/{row.name}"

    def config_key(self, item: Any) -> str:
        return f"{item.namespace}/{item.name}"

    async def find(self, ctx: ApplyContext, key: str) -> Component | None:
        namespace, name = key.split("/", 1)
        return (
            await ctx.db.execute(
                select(Component)
                .where(Component.namespace == namespace, Component.name == name)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    def spec_from_row(self, row: Component) -> ComponentSpec:
        return ComponentSpec.model_construct(
            namespace=row.namespace,
            name=row.name,
            title=row.title or None,
            description=row.description or None,
            source_code=row.source_code,
            input_schema=dict(row.input_schema or {}),
            enabled_agents=list(row.enabled_agents or []),
            enabled_functions=list(row.enabled_functions or []),
            enabled_queries=list(row.enabled_queries or []),
            enabled_components=list(row.enabled_components or []),
            enabled_stores=[
                EnabledStoreSpec.model_construct(
                    store=entry.get("store"), access=entry.get("access") or "readonly"
                )
                for entry in (row.enabled_stores or [])
                if isinstance(entry, dict)
            ],
            visibility=row.visibility or "private",
            is_active=row.is_active is not False,
        )

    def new_row(self, spec: ComponentSpec, ctx: ApplyContext) -> Component:
        return Component(user_id=uuid.UUID(str(ctx.owner_user_id)))

    def write_fields(self, row: Component, spec: ComponentSpec) -> None:
        row.namespace = spec.namespace
        row.name = spec.name
        row.title = spec.title
        row.description = spec.description
        row.source_code = spec.source_code
        row.input_schema = dict(spec.input_schema)
        row.enabled_agents = list(spec.enabled_agents)
        row.enabled_functions = list(spec.enabled_functions)
        row.enabled_queries = list(spec.enabled_queries)
        row.enabled_components = list(spec.enabled_components)
        row.enabled_stores = [entry.model_dump() for entry in spec.enabled_stores]
        row.visibility = spec.visibility
        row.is_active = spec.is_active
