"""LLM providers applier.

Admin-managed and owner-less. Deleting one through the API switches it off
(agents point at it by id); config and packages keep that state unless they
declare isActive. Packages never declare providers.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import select

from app.core.encryption import encryption_service
from app.models.llm_provider import LLMProvider
from app.schemas.spec.llm_provider import LLMProviderSpec
from app.services.resources.base import ApplyContext, ResourceApplier

logger = logging.getLogger(__name__)


class LLMProviderApplier(ResourceApplier[LLMProviderSpec]):
    kind = "llmProviders"
    label = "LLM provider"
    noun = "LLM provider"
    config_section = "llmProviders"
    spec_model = LLMProviderSpec
    model = LLMProvider
    # The key is write-only: left out, it's kept. Switched off (deleted) and
    # the default chosen in the console are operator state.
    keep_unless_declared = ("api_key", "is_active", "is_default")
    deleted_with_package = False

    def key_of(self, spec: LLMProviderSpec) -> str:
        return spec.key

    def key_of_row(self, row: LLMProvider) -> str:
        return row.name

    def config_key(self, item: Any) -> str:
        return item.name

    async def find(self, ctx: ApplyContext, key: str) -> LLMProvider | None:
        return (
            await ctx.db.execute(
                select(LLMProvider)
                .where(LLMProvider.name == key)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    def spec_from_row(self, row: LLMProvider) -> LLMProviderSpec:
        api_key: Optional[str] = None
        if row.api_key:
            try:
                api_key = encryption_service.decrypt(row.api_key)
            except Exception:  # unreadable (key changed): treated as unknown
                logger.warning("API key of LLM provider '%s' could not be decrypted", row.name)
        return LLMProviderSpec.model_construct(
            name=row.name,
            provider_type=row.provider_type,
            api_key=api_key,
            api_endpoint=row.api_endpoint or None,
            default_model=row.default_model or None,
            config=dict(row.config or {}),
            is_default=bool(row.is_default),
            is_active=row.is_active is not False,
        )

    def new_row(self, spec: LLMProviderSpec, ctx: ApplyContext) -> LLMProvider:
        return LLMProvider()

    async def write_row(
        self, row: LLMProvider, spec: LLMProviderSpec, ctx: ApplyContext,
        current: Optional[LLMProviderSpec] = None,
    ) -> None:
        if spec.is_default and not (current is not None and current.is_default):
            await self._unset_other_defaults(row, ctx)
        row.name = spec.name
        row.provider_type = spec.provider_type
        # Write-only, and never cleared by leaving it out (or sending "").
        if spec.api_key is not None:
            row.api_key = encryption_service.encrypt(spec.api_key)
        row.api_endpoint = spec.api_endpoint
        row.default_model = spec.default_model
        row.config = dict(spec.config)
        row.is_default = spec.is_default
        row.is_active = spec.is_active

    async def _unset_other_defaults(self, row: LLMProvider, ctx: ApplyContext) -> None:
        """One default provider; the previous one's change is recorded too."""
        from app.schemas.spec.base import diff_specs
        from app.services.resources.base import lock_singleton
        from app.services.resources.history import record_revision

        await lock_singleton(ctx, "default-llm-provider")
        stmt = select(LLMProvider).where(LLMProvider.is_default.is_(True)).with_for_update()
        if row.id is not None:
            stmt = stmt.where(LLMProvider.id != row.id)
        for other in (await ctx.db.execute(stmt)).scalars().all():
            before = self.spec_from_row(other)
            after = before.model_copy(update={"is_default": False})
            other.is_default = False
            state = self.history_spec(after)
            await record_revision(
                ctx, self, other, "update", state, diff_specs(self.history_spec(before), state),
                self.secret_values(after),
            )

    def readable_key(self, row: LLMProvider) -> Optional[str]:
        """The key for an export that must carry it: an unreadable one is an
        error, not a silently missing credential."""
        if not row.api_key:
            return None
        try:
            return encryption_service.decrypt(row.api_key)
        except Exception as e:
            raise ValueError(
                f"The API key of LLM provider '{row.name}' can't be decrypted "
                "(was ENCRYPTION_KEY changed?); export it without secrets or set the key again"
            ) from e

    def secret_values(self, spec: LLMProviderSpec) -> dict[str, Any]:
        return {"api_key": spec.api_key} if spec.api_key is not None else {}

    def with_secrets(self, state: dict[str, Any], secrets: dict[str, Any]) -> dict[str, Any]:
        return {**state, "api_key": secrets["api_key"]} if "api_key" in secrets else state
