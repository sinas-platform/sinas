"""Secrets applier — shared secrets.

Private secrets are a user's own credentials, not configuration: config and
packages never declare them, and they stay outside this path (and outside
change history). Shared ones are what config declares, what a package's
secret variables fill in, and what connectors and pipelines resolve.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from sqlalchemy import select

from app.core.encryption import encryption_service
from app.models.secret import Secret
from app.schemas.spec.secret import SecretSpec
from app.services.resources.base import (
    ApplierError,
    ApplyContext,
    OwnershipDecision,
    ResourceApplier,
    ownership_decision,
)

logger = logging.getLogger(__name__)


class SecretApplier(ResourceApplier[SecretSpec]):
    kind = "secrets"
    label = "Secret"
    noun = "secret"
    config_section = "secrets"
    spec_model = SecretSpec
    model = Secret
    # A config re-applied without the value (or description) keeps it.
    keep_unless_declared = ("value", "description")
    # An operator's credential outlives the package that asked for it:
    # uninstall and upgrades never deleted secrets, and still don't.
    deleted_with_package = False

    def key_of(self, spec: SecretSpec) -> str:
        return spec.key

    def key_of_row(self, row: Secret) -> str:
        return row.name

    def config_key(self, item: Any) -> str:
        return item.name

    async def find(self, ctx: ApplyContext, key: str) -> Secret | None:
        # Shared only: a private secret of the same name is another user's.
        return (
            await ctx.db.execute(
                select(Secret)
                .where(Secret.name == key, Secret.visibility == "shared")
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    def spec_from_row(self, row: Secret) -> SecretSpec:
        try:
            value: Optional[str] = encryption_service.decrypt(row.encrypted_value)
        except Exception:  # unreadable (key changed): treated as unknown
            logger.warning("Secret '%s' could not be decrypted", row.name)
            value = None
        return SecretSpec.model_construct(
            name=row.name, description=row.description or None, value=value
        )

    def new_row(self, spec: SecretSpec, ctx: ApplyContext) -> Secret:
        return Secret(user_id=uuid.UUID(str(ctx.owner_user_id)), visibility="shared")

    def write_fields(self, row: Secret, spec: SecretSpec) -> None:
        row.name = spec.name
        row.description = spec.description
        if spec.value is not None:
            row.encrypted_value = encryption_service.encrypt(spec.value)

    def ownership(self, row: Secret, ctx: ApplyContext) -> OwnershipDecision:
        # A package may fill in a secret that already exists (its secret
        # variables write it just before the package applies), as it always
        # could. Safe here, unlike other kinds: packages never delete secrets.
        if ctx.origin == "package" and row.managed_by is None:
            return "write"
        return ownership_decision(row.managed_by, ctx, row.config_name)

    async def check_references(self, spec: SecretSpec, ctx: ApplyContext) -> None:
        # Runs on create (no reference fields to change on update).
        if spec.value is None:
            raise ApplierError(
                f"Secret '{spec.name}' does not exist and no value provided — cannot create."
            )

    def secret_values(self, spec: SecretSpec) -> dict[str, Any]:
        return {"value": spec.value} if spec.value is not None else {}

    def with_secrets(self, state: dict[str, Any], secrets: dict[str, Any]) -> dict[str, Any]:
        return {**state, "value": secrets["value"]} if "value" in secrets else state
