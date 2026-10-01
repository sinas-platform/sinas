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
    changes: Optional[dict[str, Any]]
    # Full spec after the change. Omitted from list responses unless asked for.
    spec: Optional[dict[str, Any]] = None
    created_at: datetime
