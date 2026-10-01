"""Canonical spec models: one definition per resource kind.

A spec model is the single source of truth for a resource's configurable
state. The same model parses REST payloads (snake_case field names) and config
YAML (camelCase aliases), validates both identically, and serialises back to
either form. See docs/design/config-apply-unification.md §4.1.
"""

from app.schemas.spec.base import SpecModel
from app.schemas.spec.database_trigger import DatabaseTriggerSpec
from app.schemas.spec.schedule import ScheduleSpec
from app.schemas.spec.webhook import WebhookDedupSpec, WebhookSpec

__all__ = [
    "SpecModel", "DatabaseTriggerSpec", "ScheduleSpec", "WebhookDedupSpec", "WebhookSpec",
]
