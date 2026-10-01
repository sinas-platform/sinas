"""Change history for configurable resources."""

import uuid
from typing import Any, Optional

from sqlalchemy import JSON, BigInteger, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import GUID, Base, created_at


class ConfigRevision(Base):
    """One recorded change to one resource, from any write channel.

    Written in the same transaction as the change it describes, so history
    and state can never disagree. Rows are append-only. See
    docs/design/config-apply-unification.md §4.7.
    """

    __tablename__ = "config_revisions"
    __table_args__ = (
        Index("ix_config_revisions_kind_key", "resource_kind", "resource_key"),
    )

    # A global sequence, not a UUID: the log is totally ordered, and there is
    # no per-resource revision counter for concurrent writers to race on.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    resource_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_key: Mapped[str] = mapped_column(String(512), nullable=False)
    # Not a foreign key: history must outlive the row it describes. Lets
    # history follow a resource across renames.
    resource_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID(), index=True)
    action: Mapped[str] = mapped_column(String(16), nullable=False)  # create|update|delete
    # Canonical spec after the change; for a delete, the last state before it.
    # Never holds secret values (each applier redacts — ResourceApplier.history_spec).
    spec: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    changes: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    origin: Mapped[str] = mapped_column(String(16), nullable=False)  # api|config|package|startup
    actor_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID())
    actor_email: Mapped[Optional[str]] = mapped_column(String(255))
    # The resource's owner at the time — so a restore can give it back to them
    # rather than to whoever happens to restore it.
    owner_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID())
    # Set when this change was a restore of an earlier revision.
    restored_from_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    managed_by: Mapped[Optional[str]] = mapped_column(Text)
    config_name: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[created_at]
