"""Which kinds have an applier — and so can be restored from history."""

from typing import Optional

from app.services.resources.base import ResourceApplier


def _applier_classes() -> list[type[ResourceApplier]]:
    """Every migrated kind, in config dependency order."""
    from app.services.resources.connectors import ConnectorApplier
    from app.services.resources.database_triggers import DatabaseTriggerApplier
    from app.services.resources.queries import QueryApplier
    from app.services.resources.schedules import ScheduleApplier
    from app.services.resources.skills import SkillApplier
    from app.services.resources.templates import TemplateApplier
    from app.services.resources.webhooks import WebhookApplier

    return [
        ConnectorApplier, SkillApplier, QueryApplier, TemplateApplier,
        WebhookApplier, ScheduleApplier, DatabaseTriggerApplier,
    ]


def applier_for(kind: str) -> Optional[ResourceApplier]:
    """The applier for a resource kind, or None if it hasn't migrated yet."""
    for applier_class in _applier_classes():
        if applier_class.kind == kind:
            return applier_class()
    return None


def all_appliers() -> list[ResourceApplier]:
    """Every migrated kind, in config dependency order."""
    return [applier_class() for applier_class in _applier_classes()]
