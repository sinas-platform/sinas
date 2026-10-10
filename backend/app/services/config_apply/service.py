"""
Configuration apply service
Handles idempotent application of declarative configuration
"""
import hashlib
import json
import logging
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.config import (
    ConfigApplyResponse,
    ConfigApplySummary,
    ResourceChange,
    SinasConfig,
)

from app.services.config_apply.identity import apply_roles, apply_users
from app.services.config_apply.data_sources import (
    apply_database_connections,
    apply_llm_providers,
)
from app.services.config_apply.resources import (
    apply_dependencies,
    apply_pipelines,
    apply_secrets,
)
from app.services.config_apply.agents import apply_agents
from pydantic.alias_generators import to_camel

from app.services.resources import ApplyContext, SideEffectBus

logger = logging.getLogger(__name__)


class ConfigApplyService:
    """Service for applying declarative configuration"""

    def __init__(
        self,
        db: AsyncSession,
        config_name: str,
        owner_user_id: str,
        managed_by: str = "config",
        auto_commit: bool = True,
        skip_resource_types: Optional[set[str]] = None,
        prune_missing: bool = False,
    ):
        self.db = db
        self.config_name = config_name
        self.owner_user_id = owner_user_id
        self.managed_by = managed_by
        self.auto_commit = auto_commit
        self.skip_resource_types = skip_resource_types or set()
        # Remove resources this source manages but no longer declares (package
        # upgrades). Only for kinds with an applier: their removals are
        # recorded in the change history, so they can be restored.
        self.prune_missing = prune_missing
        self.summary = ConfigApplySummary()
        self.changes: list[ResourceChange] = []
        # Post-commit notifications. Collected during apply and published only
        # after the transaction commits, so a worker can never observe an event
        # for a row it cannot read yet. When auto_commit is False the caller
        # owns the commit and must call flush_notifications() itself.
        # Effects from resources already migrated to the per-resource
        # appliers (docs/design/config-apply-unification.md); the two lists
        # below are the not-yet-migrated kinds and fold into this bus as they
        # move over.
        self.effects = SideEffectBus()
        self._pending_references: dict[str, dict[str, bool]] = {}
        self.errors: list[str] = []
        self.warnings: list[str] = []

        # Resource lookup caches (name -> id)
        self.role_ids: dict[str, str] = {}
        self.user_ids: dict[str, str] = {}
        self.datasource_ids: dict[str, str] = {}
        self.agent_ids: dict[str, str] = {}
        self.llm_provider_ids: dict[str, str] = {}
        self.database_connection_ids: dict[str, str] = {}
        self.webhook_ids: dict[str, str] = {}

    def _calculate_hash(self, data: dict[str, Any]) -> str:
        """Calculate hash for change detection"""
        # Create stable JSON string and hash it
        data_str = json.dumps(data, sort_keys=True)
        return hashlib.sha256(data_str.encode()).hexdigest()

    def _track_change(
        self,
        action: str,
        resource_type: str,
        resource_name: str,
        details: Optional[str] = None,
        changes: Optional[dict[str, Any]] = None,
    ):
        """Track a resource change"""
        self.changes.append(
            ResourceChange(
                action=action,
                resourceType=resource_type,
                resourceName=resource_name,
                details=details,
                changes=changes,
            )
        )

        # Update summary - map action to summary field name
        action_field_map = {
            "create": "created",
            "update": "updated",
            "unchanged": "unchanged",
            "delete": "deleted",
        }
        summary_field = action_field_map.get(action, action)
        summary_dict = getattr(self.summary, summary_field)
        summary_dict[resource_type] = summary_dict.get(resource_type, 0) + 1


    async def flush_notifications(self) -> None:
        """Publish queued events to the scheduler and CDC workers.

        Call AFTER the transaction commits. Callers that pass auto_commit=False
        (package install, for one) own their commit and must call this, or
        config-applied schedules never reach the running scheduler and CDC
        triggers are not picked up until a restart. Best-effort throughout: a
        notification failure must not fail an apply that already committed.
        """
        await self.effects.flush()

    async def apply_config(self, config: SinasConfig, dry_run: bool = False) -> ConfigApplyResponse:
        """
        Apply configuration idempotently

        Args:
            config: Validated configuration
            dry_run: If True, don't actually apply changes

        Returns:
            ConfigApplyResponse with results
        """
        self._pending_references = {
            kind: {
                f"{item.namespace}/{item.name}": getattr(item, "isActive", True) is not False
                for item in getattr(config.spec, kind)
            }
            for kind in ("functions", "agents", "pipelines")
        }
        # A function the config leaves isActive unset on keeps its current
        # state (FunctionApplier), so a disabled one stays disabled: a preview
        # must see that, or it accepts a reference the real apply refuses.
        unset = [f for f in config.spec.functions if f.isActive is None]
        if unset:
            from sqlalchemy import or_, select

            from app.models.function import Function

            disabled = (await self.db.execute(
                select(Function.namespace, Function.name).where(
                    Function.is_active.is_(False),
                    or_(*(
                        (Function.namespace == f.namespace) & (Function.name == f.name)
                        for f in unset
                    )),
                )
            )).all()
            # A declaration that does set isActive (say, the same function
            # listed again) decides, as it does in the real apply.
            explicit = {
                f"{f.namespace}/{f.name}" for f in config.spec.functions if f.isActive is not None
            }
            for namespace, name in disabled:
                if f"{namespace}/{name}" not in explicit:
                    self._pending_references["functions"][f"{namespace}/{name}"] = False
        # Packages skip connections: one declared there is never created.
        if "databaseConnections" not in self.skip_resource_types:
            self._pending_references["databaseConnections"] = {
                item.name: True for item in config.spec.databaseConnections
            }
        try:
            # Common kwargs shared by all appliers
            common = dict(
                db=self.db,
                dry_run=dry_run,
                managed_by=self.managed_by,
                config_name=self.config_name,
                calculate_hash=self._calculate_hash,
                track_change=self._track_change,
                errors=self.errors,
                warnings=self.warnings,
            )
            common_with_owner = dict(**common, owner_user_id=self.owner_user_id)

            # Apply resources in dependency order
            if "roles" not in self.skip_resource_types:
                await apply_roles(
                    **common,
                    roles=config.spec.roles,
                    role_ids=self.role_ids,
                )
            if "users" not in self.skip_resource_types:
                await apply_users(
                    **common,
                    users=config.spec.users,
                    role_ids=self.role_ids,
                    user_ids=self.user_ids,
                )
            if "llmProviders" not in self.skip_resource_types:
                await apply_llm_providers(
                    **common,
                    providers=config.spec.llmProviders,
                    llm_provider_ids=self.llm_provider_ids,
                )
            if "databaseConnections" not in self.skip_resource_types:
                await apply_database_connections(
                    **common,
                    connections=config.spec.databaseConnections,
                    database_connection_ids=self.database_connection_ids,
                )

            if "secrets" not in self.skip_resource_types:
                await apply_secrets(
                    **common_with_owner,
                    secrets=config.spec.secrets,
                )

            if "dependencies" not in self.skip_resource_types:
                await apply_dependencies(
                    **common_with_owner,
                    dependencies=config.spec.dependencies,
                )

            if "agents" not in self.skip_resource_types:
                await apply_agents(
                    **common_with_owner,
                    agents=config.spec.agents,
                    llm_provider_ids=self.llm_provider_ids,
                    agent_ids=self.agent_ids,
                )
            # Pipelines apply after connectors/functions/queries/agents (their
            # step references), and before the triggers that may target them.
            if "pipelines" not in self.skip_resource_types:
                await apply_pipelines(
                    **common_with_owner,
                    pipelines=config.spec.pipelines,
                )
            # Kinds with a per-resource applier: connectors, functions,
            # skills, queries, templates, collections, stores, manifests,
            # components, webhooks, schedules, databaseTriggers — after
            # everything they can point at. (Nothing checks a reference to a
            # connector, function, skill or query from agents or pipelines
            # yet; when those migrate, these move ahead of them.)
            from app.services.resources.registry import all_appliers

            for applier in all_appliers():
                if applier.kind not in self.skip_resource_types:
                    await self._apply_kind(
                        applier, getattr(config.spec, applier.config_section), dry_run
                    )

            if self.prune_missing:
                await self._prune_missing(config, dry_run)

            if self.errors:
                # All or nothing. A config or package with any resource that
                # fails changes nothing at all: it used to report success and
                # commit everything else, leaving a package "installed" without
                # the parts that failed. Callers that own the transaction
                # (auto_commit=False: package install) get success=False and
                # roll back themselves; a dry run reports the same verdict the
                # real apply would reach.
                self._discard_pending()
                if not dry_run and self.auto_commit:
                    await self.db.rollback()
                return ConfigApplyResponse(
                    success=False,
                    summary=self.summary,
                    changes=self.changes,
                    errors=self.errors,
                    warnings=self.warnings,
                )

            if not dry_run and self.auto_commit:
                await self.db.commit()
                await self.flush_notifications()

            return ConfigApplyResponse(
                success=True,
                summary=self.summary,
                changes=self.changes,
                errors=self.errors,
                warnings=self.warnings,
            )

        except Exception as e:
            logger.error(f"Error applying config: {str(e)}", exc_info=True)
            await self.db.rollback()
            self._discard_pending()  # nothing committed, so nothing to announce
            return ConfigApplyResponse(
                success=False,
                summary=self.summary,
                changes=self.changes,
                errors=[f"Fatal error: {str(e)}"],
                warnings=self.warnings,
            )

    # ------------------------------------------------------------------
    # Kinds migrated to per-resource appliers
    # ------------------------------------------------------------------

    async def _prune_missing(self, config: SinasConfig, dry_run: bool) -> None:
        """Delete what this source manages but no longer declares.

        Scoped to resources stamped with this source's managed_by — and, for a
        plain config (where every file shares managed_by="config"), to its
        config_name too, or applying one file would delete another's
        resources. A resource someone edited by hand was detached from the
        package at that edit, so an upgrade never removes it. Part of the same
        all-or-nothing transaction, reported in summary.deleted, and a dry run
        lists what would go without removing anything.
        """
        from sqlalchemy import select

        from app.services.resources.registry import all_appliers

        ctx = self._resource_context(dry_run)
        for applier in all_appliers():
            if applier.kind in self.skip_resource_types:
                continue
            declared = {
                applier.config_key(item)
                for item in getattr(config.spec, applier.config_section, None) or []
            }
            model = applier.model
            stmt = select(model).where(model.managed_by == self.managed_by)
            if not self.managed_by.startswith("pkg:"):
                stmt = stmt.where(model.config_name == self.config_name)
            if not dry_run:
                # As for uninstall: a row detached by a concurrent manual edit
                # drops out of the filter once locked, and is kept.
                stmt = stmt.with_for_update().execution_options(populate_existing=True)
            for row in (await self.db.execute(stmt)).scalars().all():
                key = applier.key_of_row(row)
                if key in declared:
                    continue
                try:
                    async with self.db.begin_nested():
                        await applier.delete(row, ctx)
                except Exception as e:
                    self.errors.append(
                        f"Error removing {applier.label.lower()} '{key}': {_describe_error(e)}"
                    )
                    continue
                self._track_change("delete", applier.kind, key)

    def _discard_pending(self) -> None:
        """Forget every queued notification: the transaction won't commit."""
        self.effects.discard()

    def _resource_context(self, dry_run: bool) -> ApplyContext:
        return ApplyContext(
            db=self.db,
            origin="package" if self.managed_by.startswith("pkg:") else "config",
            actor_user_id=self.owner_user_id,
            owner_user_id=self.owner_user_id,
            managed_by=self.managed_by,
            config_name=self.config_name,
            dry_run=dry_run,
            effects=self.effects,
            pending_references=self._pending_references,
        )

    async def _apply_kind(self, applier, items: list, dry_run: bool) -> None:
        ctx = self._resource_context(dry_run)
        for item in items:
            key = applier.config_key(item)
            try:
                declared = item.model_dump(exclude_none=True)
                spec = applier.spec_model.model_validate(declared)
                keep = {
                    field for field in applier.keep_unless_declared
                    if to_camel(field) not in declared
                }
                # One savepoint per resource: a failing one is reported and
                # rolled back on its own instead of poisoning the session for
                # every resource after it.
                async with self.db.begin_nested():
                    result = await applier.apply(spec, ctx, keep=keep)
            except Exception as e:
                self.errors.append(f"Error applying {applier.noun} '{key}': {_describe_error(e)}")
                continue
            if result.warning:
                self.warnings.append(result.warning)
            self._track_change(
                "unchanged" if result.action == "skipped" else result.action,
                applier.kind,
                key,
                changes=result.changes or None,
            )


def _describe_error(error: Exception) -> str:
    """One line per problem, rather than pydantic's multi-line dump."""
    from pydantic import ValidationError

    if isinstance(error, ValidationError):
        return "; ".join(
            f"{'.'.join(str(part) for part in err['loc']) or 'spec'}: {err['msg']}"
            for err in error.errors(include_url=False)
        )
    return str(error)
