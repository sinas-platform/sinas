"""Per-resource appliers: the single write path for configurable resources.

Every channel that changes a resource — REST/console, config apply, package
install, startup apply — goes through that resource's applier. Upsert
semantics, validation, ownership, side effects and change history are defined
once here. See docs/design/config-apply-unification.md.
"""

from app.services.resources.base import (
    ApplierError,
    ApplyContext,
    ApplyResult,
    ReferenceNotFound,
    ResourceApplier,
    ResourceConflict,
    SchedulerJobChanged,
    SideEffectBus,
)

__all__ = [
    "ApplierError",
    "ApplyContext",
    "ApplyResult",
    "ReferenceNotFound",
    "ResourceApplier",
    "ResourceConflict",
    "SchedulerJobChanged",
    "SideEffectBus",
]
