"""What every REST endpoint does around an applier: parse the spec, lock the
row the permission check authorized, write, commit, then publish effects.

Kinds migrated earlier keep their own copies of these; later kinds share them.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.spec.base import SpecModel
from app.services.resources.base import ApplierError, ApplyContext, ApplyResult, ResourceApplier
from app.services.resources.patch import PatchRejected, patched_spec


def api_context(db: AsyncSession, user_id) -> ApplyContext:
    return ApplyContext(db=db, origin="api", actor_user_id=str(user_id), owner_user_id=str(user_id))


def parse_spec(applier: ResourceApplier, data: dict[str, Any]) -> SpecModel:
    try:
        return applier.spec_model.model_validate(data)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=json.loads(e.json(include_url=False)))


def patch_spec(applier: ResourceApplier, row: Any, patch: dict[str, Any], current: Optional[SpecModel] = None) -> SpecModel:
    try:
        return patched_spec(current if current is not None else applier.spec_from_row(row), patch)
    except PatchRejected as e:
        raise HTTPException(status_code=422, detail=e.detail)


async def locked(applier: ResourceApplier, ctx: ApplyContext, authorized: Any) -> Any:
    """The row the permission check authorized, locked until commit (the
    permission lookup doesn't lock).

    Re-read by id, never by key: one deleted and recreated under that key
    meanwhile is another resource. And still under the key and owner the
    check authorized: permissions are scoped by namespace/name and owner, so
    a row renamed or handed over meanwhile may be outside them."""
    key, owner = applier.key_of_row(authorized), authorized.user_id
    row = await applier.find_by_id(ctx, authorized.id)
    if row is None:  # deleted between the permission check and now
        raise HTTPException(status_code=404, detail=f"{applier.label} not found")
    if (applier.key_of_row(row), row.user_id) != (key, owner):
        raise HTTPException(
            status_code=409,
            detail=f"{applier.label} changed while this request ran; try again",
        )
    return row


async def write(applier: ResourceApplier, ctx: ApplyContext, spec: SpecModel, **kwargs) -> ApplyResult:
    try:
        return await applier.apply(spec, ctx, **kwargs)
    except ApplierError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    except IntegrityError:
        # Lost a race to a concurrent create or rename onto the same key (the
        # applier's check can't lock a row that doesn't exist yet).
        raise HTTPException(
            status_code=400, detail=f"{applier.label} '{applier.key_of(spec)}' already exists"
        )


async def commit(db: AsyncSession, ctx: ApplyContext) -> None:
    await db.commit()
    await ctx.effects.flush()
