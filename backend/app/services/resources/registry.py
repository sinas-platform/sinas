"""Which kinds have an applier — and so can be restored from history."""

from typing import Optional

from app.services.resources.base import ResourceApplier


def _applier_classes() -> list[type[ResourceApplier]]:
    """Every migrated kind, in config dependency order."""
    from app.services.resources.agents import AgentApplier
    from app.services.resources.collections import CollectionApplier
    from app.services.resources.components import ComponentApplier
    from app.services.resources.connectors import ConnectorApplier
    from app.services.resources.database_triggers import DatabaseTriggerApplier
    from app.services.resources.functions import FunctionApplier
    from app.services.resources.llm_providers import LLMProviderApplier
    from app.services.resources.manifests import ManifestApplier
    from app.services.resources.pipelines import PipelineApplier
    from app.services.resources.queries import QueryApplier
    from app.services.resources.schedules import ScheduleApplier
    from app.services.resources.secrets import SecretApplier
    from app.services.resources.skills import SkillApplier
    from app.services.resources.stores import StoreApplier
    from app.services.resources.templates import TemplateApplier
    from app.services.resources.webhooks import WebhookApplier

    return [
        SecretApplier, LLMProviderApplier, ConnectorApplier, FunctionApplier, SkillApplier, QueryApplier, TemplateApplier, CollectionApplier,
        StoreApplier, ManifestApplier, AgentApplier, PipelineApplier,
        ComponentApplier, WebhookApplier, ScheduleApplier,
        DatabaseTriggerApplier,
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
