from sqlalchemy import String, Text, Boolean, ForeignKey, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, Dict, Any
import uuid

from .base import Base, uuid_pk, created_at, updated_at


class Template(Base):
    __tablename__ = "templates"
    __table_args__ = (
        UniqueConstraint('namespace', 'name', name='uix_template_namespace_name'),
    )

    id: Mapped[uuid_pk]
    namespace: Mapped[str] = mapped_column(String(255), nullable=False, default="default", index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # Unique key like "otp_email", "function_failed_email", "sales_report_output"
    # Name indicates purpose - no need for separate type enum

    description: Mapped[Optional[str]] = mapped_column(Text)

    # Ownership for permission checks
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"), index=True)
    group_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("groups.id"), index=True)

    # Template content
    title: Mapped[Optional[str]] = mapped_column(Text) # subject for email, possibel other use cases
    html_content: Mapped[str] = mapped_column(Text, nullable=False)  # Jinja2 template
    text_content: Mapped[Optional[str]] = mapped_column(Text)  # Plain text fallback

    # Variable schema (like Function.input_schema)
    variable_schema: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    # Example:
    # {
    #   "type": "object",
    #   "properties": {
    #     "otp_code": {"type": "string", "description": "6-digit OTP code"},
    #     "user_email": {"type": "string", "format": "email"},
    #     "expiry_minutes": {"type": "integer"}
    #   },
    #   "required": ["otp_code", "user_email"]
    # }

    # Metadata
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"), index=True)
    updated_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]

    # Declarative config support
    managed_by: Mapped[Optional[str]] = mapped_column(Text)  # "config" if managed by YAML
    config_name: Mapped[Optional[str]] = mapped_column(Text)  # Config file name
    config_checksum: Mapped[Optional[str]] = mapped_column(Text)  # For change detection

    # Relationships
    owner: Mapped[Optional["User"]] = relationship("User", foreign_keys=[user_id])
    group: Mapped[Optional["Group"]] = relationship("Group", foreign_keys=[group_id])
    creator: Mapped[Optional["User"]] = relationship("User", foreign_keys=[created_by])
    updater: Mapped[Optional["User"]] = relationship("User", foreign_keys=[updated_by])
