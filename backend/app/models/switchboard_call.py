"""One tool call made through the switchboard (POST /mcp/{ns}/{name}).

The audit log the Switchboard app shows: who called what, through which
manifest, with what (redacted) arguments, and how it went. Rows are kept
for SWITCHBOARD_AUDIT_RETENTION_DAYS (scheduler job cleanup_switchboard_calls).
"""
import uuid
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, created_at, uuid_pk


class SwitchboardCall(Base):
    __tablename__ = "switchboard_calls"

    id: Mapped[uuid_pk]
    manifest_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("manifests.id", ondelete="SET NULL"), nullable=True
    )
    # Kept by name too: the row outlives the manifest.
    manifest_namespace: Mapped[str] = mapped_column(String(255), nullable=False)
    manifest_name: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(16), nullable=False)  # function | query | pipeline
    resource_ref: Mapped[str] = mapped_column(String(512), nullable=False)  # namespace/name
    arguments: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)  # redacted
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)  # clipped, redacted
    is_error: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[created_at]
