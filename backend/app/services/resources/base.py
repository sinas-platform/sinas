"""Applier foundations: context, side-effect bus, errors, ownership, base class."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, ClassVar, Generic, Literal, Optional, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

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
    # Keys defined earlier in the same apply, by kind ("functions": {"ns/x"}).
    # A dry run creates nothing, so a reference to something the same config
    # is about to create can only be satisfied from here.
    pending_references: dict[str, set[str]] = field(default_factory=dict)
    _actor_email: Optional[str] = field(default=None, repr=False)

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


def ownership_decision(row_managed_by: Optional[str], ctx: ApplyContext) -> OwnershipDecision:
    """The managed_by state machine (design §4.4), one place for every kind.

    | row managed_by | API write       | config / package apply              |
    |----------------|-----------------|-------------------------------------|
    | NULL (manual)  | write           | adopt + stamp                       |
    | same manager   | write + detach  | write, re-stamp                     |
    | other manager  | write + detach  | warn + skip                         |
    """
    if ctx.origin == "api":
        return "write_detach" if row_managed_by else "write"
    if row_managed_by is None or row_managed_by == ctx.managed_by:
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
    spec_model: ClassVar[type[SpecModel]]
    model: ClassVar[type]
    # Canonical fields that point at other resources. References are verified
    # on create and when one of these changes — not on every update, or a
    # resource whose target was deleted could no longer even be paused.
    reference_fields: ClassVar[tuple[str, ...]] = ()

    # ---- hooks -------------------------------------------------------------

    def key_of(self, spec: TSpec) -> str:
        raise NotImplementedError

    def key_of_row(self, row: Any) -> str:
        raise NotImplementedError

    async def find(self, ctx: ApplyContext, key: str) -> Any:
        raise NotImplementedError

    def spec_from_row(self, row: Any) -> TSpec:
        raise NotImplementedError

    def new_row(self, spec: TSpec, ctx: ApplyContext) -> Any:
        raise NotImplementedError

    def write_fields(self, row: Any, spec: TSpec) -> None:
        raise NotImplementedError

    async def check_references(self, spec: TSpec, ctx: ApplyContext) -> None:
        """Raise ReferenceNotFound if the spec points at something missing."""

    def effects(self, action: str, row: Any) -> list[Any]:
        return []

    def history_spec(self, spec: TSpec) -> dict[str, Any]:
        """What change history stores. Kinds with secret fields MUST override
        this to store references, never values."""
        return spec.canonical()

    # ---- shared semantics --------------------------------------------------

    async def apply(
        self,
        spec: TSpec,
        ctx: ApplyContext,
        *,
        existing: Any = None,
        must_create: bool = False,
    ) -> ApplyResult:
        """Create or update the resource described by `spec`.

        `existing`: the row being edited, when the caller already resolved it
        (a REST PATCH may rename, so the new key alone wouldn't find it).
        `must_create`: refuse to update an existing row (REST POST contract).
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
            self.write_fields(row, spec)
            self._stamp(row, spec, ctx)
            ctx.db.add(row)
            await ctx.db.flush()  # assigns the id effects and history refer to
            await record_revision(ctx, self, row, "create", new_canonical, changes)
            for effect in self.effects("create", row):
                ctx.effects.add(effect)
            return ApplyResult("create", obj=row, changes=changes)

        # ---- update ----------------------------------------------------------
        decision = ownership_decision(row.managed_by, ctx)
        if decision == "skip":
            warning = (
                f"{self.label} '{new_key}' exists but is managed by "
                f"'{row.managed_by}'. Skipping."
            )
            return ApplyResult("skipped", obj=row, warning=warning)

        changes = diff_specs(self.history_spec(self.spec_from_row(row)), new_canonical)

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

        self.write_fields(row, spec)
        if decision == "write_detach":
            row.managed_by = None
            row.config_name = None
            row.config_checksum = None
        else:
            self._stamp(row, spec, ctx)
        await ctx.db.flush()

        await record_revision(ctx, self, row, "update", new_canonical, changes)
        for effect in self.effects("update", row):
            ctx.effects.add(effect)
        return ApplyResult("update", obj=row, changes=changes)

    async def delete(self, row: Any, ctx: ApplyContext) -> None:
        """Delete a resource. History keeps its last state, so it can be
        inspected (and later restored) after the row is gone."""
        from app.services.resources.history import record_revision

        if ctx.dry_run:
            return
        last = self.history_spec(self.spec_from_row(row))
        effects = self.effects("delete", row)
        await record_revision(ctx, self, row, "delete", last, diff_specs(last, None))
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
