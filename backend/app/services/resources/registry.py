"""Which kinds have an applier — and so can be restored from history."""

from typing import Optional

from app.services.resources.base import ResourceApplier


def applier_for(kind: str) -> Optional[ResourceApplier]:
    """The applier for a resource kind, or None if it hasn't migrated yet."""
    from app.services.resources.schedules import ScheduleApplier

    appliers = {ScheduleApplier.kind: ScheduleApplier}
    applier_class = appliers.get(kind)
    return applier_class() if applier_class else None


def all_appliers() -> list[ResourceApplier]:
    """Every migrated kind, in config dependency order."""
    from app.services.resources.schedules import ScheduleApplier

    return [ScheduleApplier()]
