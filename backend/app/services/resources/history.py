"""Recording changes to configurable resources."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from app.models.config_revision import ConfigRevision

if TYPE_CHECKING:
    from app.services.resources.base import ApplyContext, ResourceApplier


async def record_revision(
    ctx: "ApplyContext",
    applier: "ResourceApplier",
    row: Any,
    action: str,
    spec: dict[str, Any] | None,
    changes: dict[str, Any] | None,
) -> ConfigRevision:
    """Append one revision, in the caller's transaction.

    Deliberately not a post-commit effect: a rolled-back change must leave no
    revision, and a committed change must always have one.
    """
    actor = ctx.actor_user_id
    revision = ConfigRevision(
        resource_kind=applier.kind,
        resource_key=applier.key_of_row(row),
        resource_id=getattr(row, "id", None),
        action=action,
        spec=spec,
        changes=changes,
        origin=ctx.origin,
        actor_user_id=uuid.UUID(str(actor)) if actor else None,
        actor_email=await ctx.actor_email(),
        managed_by=ctx.managed_by,
        config_name=ctx.config_name,
    )
    ctx.db.add(revision)
    return revision
