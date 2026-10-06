"""Applier foundations: context, side-effect bus, errors, ownership, base class."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, ClassVar, Generic, Iterable, Literal, Optional, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.config import OwnershipSkip
from app.schemas.spec.base import SpecModel, diff_specs

logger = logging.getLogger(__name__)

Origin = Literal["api", "config", "package", "startup"]
Action = Literal["create", "update", "unchanged", "skipped"]

TSpec = TypeVar("TSpec", bound=SpecModel)


# --------------------------------------------------------------- side effects


@dataclass(frozen=True)
class SchedulerJobChanged:
    """Tell the running scheduler a job changed. Actions are exactly the ones
    the listener handles (scheduler/service.py): an unknown action is dropped
    there with only a warning — which is how config-created schedules went
    unscheduled until a restart."""

    action: Literal["add", "update", "remove"]
    job_id: str

    channel: ClassVar[str] = "sinas:scheduler:jobs"

    def message(self) -> str:
        return json.dumps({"action": self.action, "job_id": self.job_id})


@dataclass(frozen=True)
class CdcTriggerChanged:
    """Tell the CDC worker a trigger changed. add/update (re)start its poll
    loop at once, remove stops it (cdc/service.py handle_trigger_change)."""

    action: Literal["add", "update", "remove"]
    trigger_id: str

    channel: ClassVar[str] = "sinas:cdc:triggers"

    def message(self) -> str:
        return json.dumps({"action": self.action, "trigger_id": self.trigger_id})


class SideEffectBus:
    """Effects recorded during a write, published only after it commits.

    Appliers never talk to Redis directly; they record intents here. Whoever
    owns the transaction calls `flush()` after commit (or `discard()` after a
    rollback), so a worker can never be told about a row it cannot read yet,
    and every channel fires the same effects.
    """

    def __init__(self) -> None:
        self._effects: list[Any] = []

    def add(self, effect: Any) -> None:
        if effect not in self._effects:  # identical intents collapse
            self._effects.append(effect)

    @property
    def pending(self) -> list[Any]:
        return list(self._effects)

    def discard(self) -> None:
        self._effects.clear()

    async def flush(self) -> None:
        """Publish and clear. Best-effort: the write already committed, so a
        failed notification must not turn into a failed request."""
        effects, self._effects = self._effects, []
        if not effects:
            return
        try:
            from app.core.redis import get_redis

            redis = await get_redis()
            for effect in effects:
                await redis.publish(effect.channel, effect.message())
        except Exception as e:  # pragma: no cover - logged, never raised
            logger.warning(f"Failed to publish side effects {effects}: {e}")


# --------------------------------------------------------------- context + results


@dataclass
class ApplyContext:
    """Who is writing, through which channel, under which manager."""

    db: AsyncSession
    origin: Origin
    actor_user_id: Optional[str] = None
    owner_user_id: Optional[str] = None
    managed_by: Optional[str] = None  # None for API writes; "config" / "pkg:<name>"
    config_name: Optional[str] = None
    dry_run: bool = False
    effects: SideEffectBus = field(default_factory=SideEffectBus)
    # Reference checks are scoped to this user where the channel requires it
    # (the REST API only lets you target your own functions). None = any owner.
    reference_scope_user_id: Optional[str] = None
    # What the same apply declares, by kind: {"pipelines": {"ns/x": is_active}}.
    # A dry run creates nothing, so a reference to something the same config
    # is about to create can only be satisfied from here (see `declared`).
    pending_references: dict[str, dict[str, bool]] = field(default_factory=dict)
    # Restores: bring a deleted resource back under its original id (so its
    # history stays one timeline), and mark the revision as a restore.
    restore_resource_id: Optional[Any] = None
    restored_from_id: Optional[int] = None
    _actor_email: Optional[str] = field(default=None, repr=False)

    def declared(self, kind: str, key: str, *, active: bool = False) -> bool:
        """In a dry run: does this apply declare `key` — and, with `active`,
        declare it active? A preview must accept exactly what the real apply
        will find once the declared resources exist, no more: a pipeline
        declared with isActive: false fails the real apply's active check."""
        if not self.dry_run:
            return False
        declared = self.pending_references.get(kind, {})
        return key in declared and (declared[key] or not active)

    async def actor_email(self) -> Optional[str]:
        if self._actor_email is None and self.actor_user_id:
            from sqlalchemy import select

            from app.models.user import User

            self._actor_email = (
                await self.db.execute(select(User.email).where(User.id == self.actor_user_id))
            ).scalar_one_or_none()
        return self._actor_email


@dataclass
class ApplyResult:
    action: Action
    obj: Any = None
    changes: dict[str, Any] = field(default_factory=dict)
    warning: Optional[str] = None
    revision: Any = None  # the ConfigRevision recorded, if any


# --------------------------------------------------------------- errors


class ApplierError(Exception):
    """A write the applier refuses. `status_code` is what the REST channel
    returns; config apply reports the message as a per-resource error."""

    status_code = 400


class ResourceConflict(ApplierError):
    status_code = 400  # schedules have always answered 400 here; kept


class ReferenceNotFound(ApplierError):
    status_code = 404


# --------------------------------------------------------------- ownership


OwnershipDecision = Literal["write", "write_detach", "skip"]


def _same_source(row_managed_by: str, row_config_name: Optional[str], ctx: ApplyContext) -> bool:
    """Every plain config file is managed_by="config"; its config_name tells
    them apart (a package's managed_by alone identifies it). Without this,
    applying one config file rewrote what another one declares. A row with no
    config_name predates the stamp and belongs to whichever config claims it."""
    if row_managed_by != ctx.managed_by:
        return False
    return (
        row_managed_by.startswith("pkg:")
        or row_config_name is None
        or row_config_name == ctx.config_name
    )


def ownership_decision(
    row_managed_by: Optional[str], ctx: ApplyContext, row_config_name: Optional[str] = None
) -> OwnershipDecision:
    """The managed_by state machine (design §4.4), one place for every kind.

    | row managed_by | API write       | config apply     | package install  |
    |----------------|-----------------|------------------|------------------|
    | NULL (manual)  | write           | adopt + stamp    | warn + skip      |
    | same manager   | write + detach  | write, re-stamp  | write, re-stamp  |
    | other manager  | write + detach  | warn + skip      | warn + skip      |

    A package never adopts a manual row: once adopted, uninstalling the
    package (or an upgrade that drops it) would delete something an operator
    made by hand. Config apply is the operator's own declaration, so it may.
    """
    if ctx.origin == "api":
        return "write_detach" if row_managed_by else "write"
    if row_managed_by is None:
        return "skip" if ctx.origin == "package" else "write"
    if _same_source(row_managed_by, row_config_name, ctx):
        return "write"
    return "skip"


# --------------------------------------------------------------- applier


class ResourceApplier(Generic[TSpec]):
    """Upsert / delete for one resource kind, identical on every channel.

    Subclasses provide the kind-specific hooks; `apply` and `delete` hold the
    shared semantics: ownership, change detection, history, side effects.
    """

    kind: ClassVar[str]
    label: ClassVar[str]  # "Schedule" — used in error messages
    noun: ClassVar[str]  # "schedule" — in config apply's per-resource errors
    config_section: ClassVar[str]  # attribute of ConfigSpec holding this kind
    spec_model: ClassVar[type[SpecModel]]
    model: ClassVar[type]
    # Canonical fields that point at other resources. References are verified
    # on create and when one of these changes — not on every update, or a
    # resource whose target was deleted could no longer even be paused.
    reference_fields: ClassVar[tuple[str, ...]] = ()
    # Operator state a config sets only when it says so: left out of the
    # YAML, an existing resource keeps its value (see apply's `keep`).
    keep_unless_declared: ClassVar[tuple[str, ...]] = ()

    # ---- hooks -------------------------------------------------------------

    def key_of(self, spec: TSpec) -> str:
        raise NotImplementedError

    def key_of_row(self, row: Any) -> str:
        raise NotImplementedError

    def config_key(self, item: Any) -> str:
        """The key a config entry declares, read without validating it, so
        deciding what a package still ships never depends on whether each
        entry happens to parse."""
        raise NotImplementedError

    async def find(self, ctx: ApplyContext, key: str) -> Any:
        raise NotImplementedError

    async def find_by_id(self, ctx: ApplyContext, resource_id: Any) -> Any:
        return await ctx.db.get(
            self.model, resource_id, with_for_update=True, populate_existing=True
        )

    def spec_from_row(self, row: Any) -> TSpec:
        raise NotImplementedError

    async def current_spec(self, ctx: ApplyContext, row: Any) -> TSpec:
        """The row's state as a spec. Override where that needs a lookup
        (a reference stored by id but declared by name)."""
        return self.spec_from_row(row)

    def new_row(self, spec: TSpec, ctx: ApplyContext) -> Any:
        raise NotImplementedError

    def write_fields(self, row: Any, spec: TSpec) -> None:
        raise NotImplementedError

    async def write_row(
        self, row: Any, spec: TSpec, ctx: ApplyContext, current: Optional[TSpec] = None
    ) -> None:
        """Write the spec onto the row. Override where that needs a lookup.
        `current` is the state the change was computed against (None on
        create): a reference held by id that the spec leaves as it was must
        stay that id, whatever its name resolves to by now."""
        self.write_fields(row, spec)

    async def check_references(self, spec: TSpec, ctx: ApplyContext) -> None:
        """Raise ReferenceNotFound if the spec points at something missing."""

    def effects(self, action: str, row: Any) -> list[Any]:
        return []

    def history_spec(self, spec: TSpec) -> dict[str, Any]:
        """What change history shows (and diffs). Kinds with secret-bearing
        fields MUST override this to redact them (history.redact) and return
        the real values from `secret_values`."""
        return spec.canonical()

    def secret_values(self, spec: TSpec) -> dict[str, Any]:
        """The values `history_spec` redacts, keyed as `with_secrets` expects.
        Stored encrypted beside the revision, for restores only."""
        return {}

    def with_secrets(self, state: dict[str, Any], secrets: dict[str, Any]) -> dict[str, Any]:
        """A recorded (redacted) state with its secret values put back."""
        return state

    # ---- shared semantics --------------------------------------------------

    async def apply(
        self,
        spec: TSpec,
        ctx: ApplyContext,
        *,
        existing: Any = None,
        must_create: bool = False,
        keep: Iterable[str] = (),
    ) -> ApplyResult:
        """Create or update the resource described by `spec`.

        `existing`: the row being edited, when the caller already resolved it
        (a REST PATCH may rename, so the new key alone wouldn't find it).
        `must_create`: refuse to update an existing row (REST POST contract).
        `keep`: fields the caller didn't set; an existing row keeps its value.
        """
        from app.services.resources.history import record_revision

        new_key = self.key_of(spec)
        row = existing if existing is not None else await self.find(ctx, new_key)

        if row is not None and must_create:
            raise ResourceConflict(f"{self.label} '{new_key}' already exists")

        # A rename must not collide with another row of the same kind.
        if row is not None and self.key_of_row(row) != new_key:
            clash = await self.find(ctx, new_key)
            if clash is not None and clash is not row:
                raise ResourceConflict(f"{self.label} '{new_key}' already exists")

        current = await self.current_spec(ctx, row) if row is not None else None
        keep = set(keep)
        if current is not None and keep:
            spec = spec.model_copy(update={field: getattr(current, field) for field in keep})
        new_canonical = self.history_spec(spec)

        # ---- create ----------------------------------------------------------
        if row is None:
            changes = diff_specs(None, new_canonical)
            # Checked in dry runs too: a preview must refuse what the real
            # apply would refuse (e.g. an inactive target), or a package
            # preview says "fine" for an install that then fails.
            await self.check_references(spec, ctx)
            if ctx.dry_run:
                return ApplyResult("create", changes=changes)
            row = self.new_row(spec, ctx)
            if ctx.restore_resource_id is not None:
                row.id = ctx.restore_resource_id
            await self.write_row(row, spec, ctx, None)
            self._stamp(row, spec, ctx)
            ctx.db.add(row)
            await ctx.db.flush()  # assigns the id effects and history refer to
            revision = await record_revision(
                ctx, self, row, "create", new_canonical, changes, self.secret_values(spec)
            )
            for effect in self.effects("create", row):
                ctx.effects.add(effect)
            return ApplyResult("create", obj=row, changes=changes, revision=revision)

        # ---- update ----------------------------------------------------------
        decision = ownership_decision(row.managed_by, ctx, getattr(row, "config_name", None))
        if decision == "skip":
            if row.managed_by is None:
                warning = (
                    f"{self.label} '{new_key}' exists and was created or edited by "
                    f"hand; '{ctx.managed_by}' leaves it as is."
                )
            else:
                manager = row.managed_by
                if manager == "config" and getattr(row, "config_name", None):
                    manager = f"config '{row.config_name}'"
                else:
                    manager = f"'{manager}'"
                warning = (
                    f"{self.label} '{new_key}' exists but is managed by "
                    f"{manager}. Skipping."
                )
            return ApplyResult("skipped", obj=row, warning=OwnershipSkip(warning))

        changes = diff_specs(self.history_spec(current), new_canonical)

        if not changes:
            # Nothing about the resource changes. A config/package apply may
            # still need ownership bookkeeping (adopting a manual row, or a
            # checksum from an older format) — never a revision or an effect.
            # An API no-op leaves the row alone entirely, including its
            # managed_by: only an actual manual change detaches it.
            if ctx.origin != "api" and not ctx.dry_run and self._needs_restamp(row, spec, ctx):
                self._stamp(row, spec, ctx)
                await ctx.db.flush()
            return ApplyResult("unchanged", obj=row)

        if any(field in changes for field in self.reference_fields):
            await self.check_references(spec, ctx)

        if ctx.dry_run:
            return ApplyResult("update", obj=row, changes=changes)

        await self.write_row(row, spec, ctx, current)
        if decision == "write_detach":
            row.managed_by = None
            row.config_name = None
            row.config_checksum = None
        else:
            self._stamp(row, spec, ctx)
        await ctx.db.flush()

        revision = await record_revision(
            ctx, self, row, "update", new_canonical, changes, self.secret_values(spec)
        )
        for effect in self.effects("update", row):
            ctx.effects.add(effect)
        return ApplyResult("update", obj=row, changes=changes, revision=revision)

    async def delete(self, row: Any, ctx: ApplyContext) -> None:
        """Delete a resource. History keeps its last state, so it can be
        inspected (and later restored) after the row is gone."""
        from app.services.resources.history import record_revision

        if ctx.dry_run:
            return
        current = await self.current_spec(ctx, row)
        last = self.history_spec(current)
        effects = self.effects("delete", row)
        await record_revision(
            ctx, self, row, "delete", last, diff_specs(last, None), self.secret_values(current)
        )
        await ctx.db.delete(row)
        await ctx.db.flush()
        for effect in effects:
            ctx.effects.add(effect)

    # ---- internals ---------------------------------------------------------

    def _stamp(self, row: Any, spec: TSpec, ctx: ApplyContext) -> None:
        if ctx.origin == "api":
            return  # manual rows stay unmanaged
        row.managed_by = ctx.managed_by
        row.config_name = ctx.config_name
        row.config_checksum = spec.checksum()

    def _needs_restamp(self, row: Any, spec: TSpec, ctx: ApplyContext) -> bool:
        return (
            row.managed_by != ctx.managed_by
            or row.config_name != ctx.config_name
            or row.config_checksum != spec.checksum()
        )
