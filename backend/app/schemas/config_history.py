"""Change history API schemas."""

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class ConfigRevisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    resource_kind: str
    resource_key: str
    resource_id: Optional[uuid.UUID]
    action: str
    origin: str
    actor_user_id: Optional[uuid.UUID]
    actor_email: Optional[str]
    managed_by: Optional[str]
    config_name: Optional[str]
    owner_user_id: Optional[uuid.UUID] = None
    restored_from_id: Optional[int] = None
    # Names of the fields this change touched — always present, always small.
    changed_fields: list[str] = []
    # The field-level values (`{field: {from, to}}`) and the full spec after
    # the change. Field values can be large (agent prompts, input data), so the
    # list endpoint leaves both out unless include_details=true; the detail
    # endpoint always includes them.
    changes: Optional[dict[str, Any]] = None
    spec: Optional[dict[str, Any]] = None
    created_at: datetime

    @classmethod
    def from_revision(cls, revision: Any, *, details: bool) -> "ConfigRevisionResponse":
        response = cls.model_validate(revision)
        response.changed_fields = sorted((revision.changes or {}).keys())
        if not details:
            response.changes = None
            response.spec = None
        return response


class ConfigRestoreResponse(BaseModel):
    """What a restore did. `action` is "create" (a deleted resource is back),
    "update" (reverted to the recorded state) or "unchanged" (it already was)."""

    action: str
    resource_kind: str
    resource_key: str
    resource_id: Optional[uuid.UUID]
    # The revision the restore itself recorded; None when nothing changed.
    revision: Optional[ConfigRevisionResponse] = None
