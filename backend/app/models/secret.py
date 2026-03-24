"""Secret model — write-only credential store."""
import uuid
from typing import Optional

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, created_at, updated_at, uuid_pk


class Secret(Base):
    """Encrypted secret. Values are write-only — never returned via API."""

    __tablename__ = "secrets"

    id: Mapped[uuid_pk]
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    encrypted_value: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    visibility: Mapped[str] = mapped_column(
        String(20), nullable=False, default="shared", server_default="shared"
    )  # "shared" (global) or "private" (per-user)

    # Config management
    managed_by: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    config_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    config_checksum: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]

    __table_args__ = (
        # Only one shared secret per name
        Index(
            "uq_secret_shared_name",
            "name",
            unique=True,
            postgresql_where=text("visibility = 'shared'"),
        ),
        # Only one private secret per user+name
        UniqueConstraint("user_id", "name", "visibility", name="uq_secret_user_name_visibility"),
    )
