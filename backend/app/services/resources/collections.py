"""Collections applier."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from app.models.file import Collection
from app.schemas.spec.collection import CollectionSpec
from app.services.resources.base import ApplyContext, ReferenceNotFound, ResourceApplier


class CollectionApplier(ResourceApplier[CollectionSpec]):
    kind = "collections"
    label = "Collection"
    noun = "collection"
    config_section = "collections"
    spec_model = CollectionSpec
    model = Collection
    reference_fields = ("content_filter_function", "post_upload_function")

    def key_of(self, spec: CollectionSpec) -> str:
        return spec.key

    def key_of_row(self, row: Collection) -> str:
        return f"{row.namespace}/{row.name}"

    def config_key(self, item: Any) -> str:
        return f"{item.namespace}/{item.name}"

    async def find(self, ctx: ApplyContext, key: str) -> Collection | None:
        namespace, name = key.split("/", 1)
        return (
            await ctx.db.execute(
                select(Collection)
                .where(Collection.namespace == namespace, Collection.name == name)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    def spec_from_row(self, row: Collection) -> CollectionSpec:
        return CollectionSpec.model_construct(
            namespace=row.namespace,
            name=row.name,
            metadata_schema=dict(row.metadata_schema or {}),
            content_filter_function=row.content_filter_function or None,
            post_upload_function=row.post_upload_function or None,
            max_file_size_mb=row.max_file_size_mb,
            max_total_size_gb=row.max_total_size_gb,
            is_public=bool(row.is_public),
            allow_shared_files=row.allow_shared_files is not False,
            allow_private_files=row.allow_private_files is not False,
        )

    def new_row(self, spec: CollectionSpec, ctx: ApplyContext) -> Collection:
        return Collection(user_id=uuid.UUID(str(ctx.owner_user_id)))

    def write_fields(self, row: Collection, spec: CollectionSpec) -> None:
        row.namespace = spec.namespace
        row.name = spec.name
        row.metadata_schema = dict(spec.metadata_schema)
        row.content_filter_function = spec.content_filter_function
        row.post_upload_function = spec.post_upload_function
        row.max_file_size_mb = spec.max_file_size_mb
        row.max_total_size_gb = spec.max_total_size_gb
        row.is_public = spec.is_public
        row.allow_shared_files = spec.allow_shared_files
        row.allow_private_files = spec.allow_private_files

    async def delete(self, row: Collection, ctx: ApplyContext) -> None:
        """Its files and versions go with it (database cascade); their stored
        bytes are removed after the commit — they used to stay behind."""
        from app.models.file import File, FileVersion
        from app.services.resources.base import StoredFilesRemoved

        paths = []
        if not ctx.dry_run:
            paths = (
                await ctx.db.execute(
                    select(FileVersion.storage_path)
                    .join(File, FileVersion.file_id == File.id)
                    .where(File.collection_id == row.id)
                )
            ).scalars().all()
        await super().delete(row, ctx)
        if paths:
            ctx.effects.add(StoredFilesRemoved(tuple(sorted(set(paths)))))

    async def check_references(self, spec: CollectionSpec, ctx: ApplyContext) -> None:
        """The upload hooks must name functions that exist. Config apply
        checked this in its parser pre-pass only; REST never did, and an
        upload into such a collection then failed with a 404."""
        from app.models.function import Function

        for ref in (spec.content_filter_function, spec.post_upload_function):
            if ref is None or ctx.declared("functions", ref):
                continue
            namespace, name = ref.split("/", 1)
            if await Function.get_by_name(ctx.db, namespace, name) is None:
                raise ReferenceNotFound(f"Function '{ref}' not found")
