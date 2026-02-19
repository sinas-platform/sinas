import uuid
from typing import Optional

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, String, Text, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, created_at, updated_at, uuid_pk
from .mixins import PermissionMixin


class User(Base, PermissionMixin):
    __tablename__ = "users"

    id: Mapped[uuid_pk]
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    last_login_at: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]

    # Config tracking
    managed_by: Mapped[Optional[str]] = mapped_column(Text)
    config_name: Mapped[Optional[str]] = mapped_column(Text)
    config_checksum: Mapped[Optional[str]] = mapped_column(Text)

    # External auth (null = OTP user)
    external_user_id: Mapped[Optional[str]] = mapped_column(String(255), unique=True, index=True)
    external_metadata: Mapped[Optional[dict]] = mapped_column(JSON)
    last_external_sync: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True))

    # Relationships
    role_memberships: Mapped[list["UserRole"]] = relationship(
        "UserRole", back_populates="user", foreign_keys="[UserRole.user_id]"
    )
    api_keys: Mapped[list["APIKey"]] = relationship(
        "APIKey", back_populates="user", foreign_keys="[APIKey.user_id]"
    )
    chats: Mapped[list["Chat"]] = relationship("Chat", back_populates="user")
    agents: Mapped[list["Agent"]] = relationship("Agent", back_populates="user")
    states: Mapped[list["State"]] = relationship("State", back_populates="user")
    collections: Mapped[list["Collection"]] = relationship("Collection", back_populates="user")
    files: Mapped[list["File"]] = relationship("File", back_populates="user")

    def can_user_access(
        self,
        user_id: str,
        permissions: dict[str, bool],
        action: str,
    ) -> bool:
        """Override: Users own themselves via id, not user_id."""
        from app.core.permissions import check_permission

        # Check :all
        if check_permission(permissions, f"sinas.users.{action}:all"):
            return True

        # Check :own (user can access their own record)
        if check_permission(permissions, f"sinas.users.{action}:own"):
            return str(self.id) == user_id

        return False


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[uuid_pk]
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    email_domain: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]

    # Config tracking
    managed_by: Mapped[Optional[str]] = mapped_column(Text)
    config_name: Mapped[Optional[str]] = mapped_column(Text)
    config_checksum: Mapped[Optional[str]] = mapped_column(Text)

    # External role mapping (1:1) - for SSO integration
    external_group_id: Mapped[Optional[str]] = mapped_column(String(255), unique=True, index=True)

    # Relationships
    members: Mapped[list["UserRole"]] = relationship(
        "UserRole", back_populates="role", cascade="all, delete-orphan"
    )
    permissions: Mapped[list["RolePermission"]] = relationship(
        "RolePermission", back_populates="role", cascade="all, delete-orphan"
    )

    @classmethod
    async def get_by_name(cls, db: AsyncSession, name: str) -> Optional["Role"]:
        """Get role by name."""
        result = await db.execute(select(cls).where(cls.name == name))
        return result.scalar_one_or_none()


class UserRole(Base):
    __tablename__ = "user_roles"

    id: Mapped[uuid_pk]
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    added_at: Mapped[created_at]
    added_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    removed_at: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True))
    removed_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))

    # Relationships
    role: Mapped["Role"] = relationship("Role", back_populates="members")
    user: Mapped["User"] = relationship(
        "User", back_populates="role_memberships", foreign_keys=[user_id]
    )


class RolePermission(Base):
    __tablename__ = "role_permissions"
    __table_args__ = (Index("ix_role_permission_unique", "role_id", "permission_key", unique=True),)

    id: Mapped[uuid_pk]
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id"), nullable=False, index=True)
    permission_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    permission_value: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]

    # Relationships
    role: Mapped["Role"] = relationship("Role", back_populates="permissions")


class OTPSession(Base):
    __tablename__ = "otp_sessions"

    id: Mapped[uuid_pk]
    email: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    otp_code: Mapped[str] = mapped_column(String(10), nullable=False)
    expires_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[created_at]


class APIKey(Base, PermissionMixin):
    __tablename__ = "api_keys"

    id: Mapped[uuid_pk]
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    permissions: Mapped[dict[str, bool]] = mapped_column(JSON, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    last_used_at: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[created_at]
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    revoked_at: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="api_keys", foreign_keys=[user_id])


class RefreshToken(Base):
    """
    Refresh tokens for JWT authentication.
    Stored in database for revocation control (logout, user deactivation).
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[uuid_pk]
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    is_revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    expires_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    last_used_at: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[created_at]
    revoked_at: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True))

    # Relationships
    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])
