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
    secrets: dict[str, Any] | None = None,
) -> ConfigRevision:
    """Append one revision, in the caller's transaction.

    Deliberately not a post-commit effect: a rolled-back change must leave no
    revision, and a committed change must always have one.
    """
    actor = ctx.actor_user_id
    owner = getattr(row, "user_id", None)
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
        owner_user_id=uuid.UUID(str(owner)) if owner else None,
        restored_from_id=ctx.restored_from_id,
        secret_state=_encrypt(secrets) if secrets else None,
    )
    ctx.db.add(revision)
    return revision


def _encrypt(secrets: dict[str, Any]) -> str:
    import json

    from app.core.encryption import encryption_service

    return encryption_service.encrypt(json.dumps(secrets, sort_keys=True))


def decrypt_secret_state(secret_state: str | None) -> dict[str, Any]:
    import json

    from app.core.encryption import encryption_service

    return json.loads(encryption_service.decrypt(secret_state)) if secret_state else {}


def redact(value: str) -> str:
    """A stand-in for a secret value in history: stable for equal values (so
    a changed value still shows as a change) but keyed, so a short secret
    can't be recovered by hashing guesses."""
    import hashlib
    import hmac

    from app.core.config import settings

    digest = hmac.new(settings.secret_key.encode(), value.encode(), hashlib.sha256).hexdigest()
    return f"<redacted:{digest[:16]}>"
