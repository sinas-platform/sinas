from sqlalchemy import String, Boolean, DateTime, ForeignKey, Index, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import Optional, List, Dict
import uuid

from .base import Base, uuid_pk, created_at, updated_at


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid_pk]
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]

    # Relationships
    group_memberships: Mapped[List["GroupMember"]] = relationship(
        "GroupMember", back_populates="user", foreign_keys="[GroupMember.user_id]"
    )
    api_keys: Mapped[List["APIKey"]] = relationship(
        "APIKey", back_populates="user", foreign_keys="[APIKey.user_id]"
    )
    chats: Mapped[List["Chat"]] = relationship("Chat", back_populates="user")
    assistants: Mapped[List["Assistant"]] = relationship("Assistant", back_populates="user")
    context_stores: Mapped[List["ContextStore"]] = relationship("ContextStore", back_populates="user")


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[uuid_pk]
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    email_domain: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]

    # Relationships
    members: Mapped[List["GroupMember"]] = relationship(
        "GroupMember", back_populates="group", cascade="all, delete-orphan"
    )
    permissions: Mapped[List["GroupPermission"]] = relationship(
        "GroupPermission", back_populates="group", cascade="all, delete-orphan"
    )
    data_sources: Mapped[List["DataSource"]] = relationship(
        "DataSource", back_populates="group", cascade="all, delete-orphan"
    )
    concepts: Mapped[List["Concept"]] = relationship(
        "Concept", back_populates="group", cascade="all, delete-orphan"
    )
    context_stores: Mapped[List["ContextStore"]] = relationship(
        "ContextStore", back_populates="group", cascade="all, delete-orphan"
    )


class GroupMember(Base):
    __tablename__ = "group_members"

    id: Mapped[uuid_pk]
    group_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("groups.id"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    role: Mapped[Optional[str]] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    added_at: Mapped[created_at]
    added_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    removed_at: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True))
    removed_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))

    # Relationships
    group: Mapped["Group"] = relationship("Group", back_populates="members")
    user: Mapped["User"] = relationship("User", back_populates="group_memberships", foreign_keys=[user_id])


class GroupPermission(Base):
    __tablename__ = "group_permissions"
    __table_args__ = (
        Index('ix_group_permission_unique', 'group_id', 'permission_key', unique=True),
    )

    id: Mapped[uuid_pk]
    group_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("groups.id"), nullable=False, index=True)
    permission_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    permission_value: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]

    # Relationships
    group: Mapped["Group"] = relationship("Group", back_populates="permissions")


class OTPSession(Base):
    __tablename__ = "otp_sessions"

    id: Mapped[uuid_pk]
    email: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    otp_code: Mapped[str] = mapped_column(String(10), nullable=False)
    expires_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[created_at]


class APIKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid_pk]
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    permissions: Mapped[Dict[str, bool]] = mapped_column(JSON, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    last_used_at: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[created_at]
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    revoked_at: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="api_keys", foreign_keys=[user_id])
