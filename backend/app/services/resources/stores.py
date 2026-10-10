"""Stores applier."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from app.models.store import Store
from app.schemas.spec.store import StoreSpec
from app.services.resources.base import ApplyContext, ResourceApplier


class StoreApplier(ResourceApplier[StoreSpec]):
    kind = "stores"
    label = "Store"
    noun = "store"
    config_section = "stores"
    spec_model = StoreSpec
    model = Store

    def key_of(self, spec: StoreSpec) -> str:
        return spec.key

    def key_of_row(self, row: Store) -> str:
        return f"{row.namespace}/{row.name}"

    def config_key(self, item: Any) -> str:
        return f"{item.namespace}/{item.name}"

    async def find(self, ctx: ApplyContext, key: str) -> Store | None:
        namespace, name = key.split("/", 1)
        return (
            await ctx.db.execute(
                select(Store)
                .where(Store.namespace == namespace, Store.name == name)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    def spec_from_row(self, row: Store) -> StoreSpec:
        return StoreSpec.model_construct(
            namespace=row.namespace,
            name=row.name,
            description=row.description or None,
            schema=dict(row.schema or {}),
            strict=bool(row.strict),
            default_visibility=row.default_visibility or "private",
            encrypted=bool(row.encrypted),
        )

    def new_row(self, spec: StoreSpec, ctx: ApplyContext) -> Store:
        return Store(user_id=uuid.UUID(str(ctx.owner_user_id)))

    def write_fields(self, row: Store, spec: StoreSpec) -> None:
        row.namespace = spec.namespace
        row.name = spec.name
        row.description = spec.description
        row.schema = dict(spec.schema)
        row.strict = spec.strict
        row.default_visibility = spec.default_visibility
        row.encrypted = spec.encrypted
