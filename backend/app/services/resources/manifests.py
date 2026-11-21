"""Manifests applier."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from app.models.manifest import Manifest
from app.schemas.spec.manifest import ManifestSpec
from app.services.resources.base import ApplyContext, ReferenceNotFound, ResourceApplier


class ManifestApplier(ResourceApplier[ManifestSpec]):
    kind = "manifests"
    label = "Manifest"
    noun = "manifest"
    config_section = "manifests"
    spec_model = ManifestSpec
    model = Manifest
    # Config apply used to switch every manifest it touched back on.
    keep_unless_declared = ("is_active",)
    # A changed exposure re-runs the existence check of what it names.
    reference_fields = ("exposed_tools",)

    def key_of(self, spec: ManifestSpec) -> str:
        return spec.key

    def key_of_row(self, row: Manifest) -> str:
        return f"{row.namespace}/{row.name}"

    def config_key(self, item: Any) -> str:
        return f"{item.namespace}/{item.name}"

    async def find(self, ctx: ApplyContext, key: str) -> Manifest | None:
        namespace, name = key.split("/", 1)
        return (
            await ctx.db.execute(
                select(Manifest)
                .where(Manifest.namespace == namespace, Manifest.name == name)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    def spec_from_row(self, row: Manifest) -> ManifestSpec:
        from app.schemas.spec.manifest import ExposedTool, SwitchboardLimits, RequiredResource, StoreDependency

        return ManifestSpec.model_construct(
            namespace=row.namespace,
            name=row.name,
            description=row.description or None,
            required_resources=[
                RequiredResource.model_construct(**r) for r in (row.required_resources or [])
            ],
            required_permissions=list(row.required_permissions or []),
            optional_permissions=list(row.optional_permissions or []),
            exposed_namespaces={k: list(v) for k, v in (row.exposed_namespaces or {}).items()},
            store_dependencies=[
                StoreDependency.model_construct(**{"key": None, **d})
                for d in (row.store_dependencies or [])
            ],
            public_info=dict(row.public_info or {}),
            exposed_tools=[
                ExposedTool.model_construct(**{"tool_name": None, "description": None, **t})
                for t in (row.exposed_tools or [])
            ],
            switchboard_limits=SwitchboardLimits.model_construct(**row.switchboard_limits) if row.switchboard_limits else None,
            is_active=row.is_active is not False,
        )

    async def check_references(self, spec: ManifestSpec, ctx: ApplyContext) -> None:
        """An exposed tool names a function, query or pipeline that exists —
        or that the same config declares. (requiredResources are checked by
        the config parser's pre-pass, as before.)"""
        from app.models.function import Function
        from app.models.pipeline import Pipeline
        from app.models.query import Query

        kinds = {"function": ("functions", Function), "query": ("queries", Query), "pipeline": ("pipelines", Pipeline)}
        for tool in spec.exposed_tools:
            kind, model = kinds[tool.type]
            if ctx.declared(kind, tool.ref):
                continue
            if await model.get_by_name(ctx.db, namespace=tool.namespace, name=tool.name) is None:
                raise ReferenceNotFound(
                    f"Exposed tool {tool.type} '{tool.ref}' not found (create it first, or declare it in the same config)"
                )

    def new_row(self, spec: ManifestSpec, ctx: ApplyContext) -> Manifest:
        return Manifest(user_id=uuid.UUID(str(ctx.owner_user_id)))

    def write_fields(self, row: Manifest, spec: ManifestSpec) -> None:
        row.namespace = spec.namespace
        row.name = spec.name
        row.description = spec.description
        row.required_resources = [r.model_dump() for r in spec.required_resources]
        row.required_permissions = list(spec.required_permissions)
        row.optional_permissions = list(spec.optional_permissions)
        row.exposed_namespaces = {k: list(v) for k, v in spec.exposed_namespaces.items()}
        row.store_dependencies = [d.model_dump() for d in spec.store_dependencies]
        row.public_info = dict(spec.public_info)
        row.exposed_tools = [t.model_dump(exclude_none=True) for t in spec.exposed_tools]
        row.switchboard_limits = spec.switchboard_limits.model_dump() if spec.switchboard_limits else None
        row.is_active = spec.is_active
