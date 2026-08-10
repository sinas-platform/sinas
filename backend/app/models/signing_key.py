"""Persisted JWT signing keypairs (RS256 auto-generated mode).

One row per purpose; today only "access-token". Written once on first boot
when JWT_ALGORITHM=RS256 and no key is supplied via env/file, then read by
every process so they all sign and verify with the same key. Access happens
via raw asyncpg in app.core.token_signing (sync-context loading); this model
exists for schema completeness and admin introspection.
"""
from typing import Optional

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, created_at, uuid_pk


class JWTSigningKey(Base):
    __tablename__ = "jwt_signing_keys"

    id: Mapped[uuid_pk]
    purpose: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    algorithm: Mapped[str] = mapped_column(String(10), nullable=False)
    kid: Mapped[str] = mapped_column(String(100), nullable=False)
    # PEM, encrypted with the platform ENCRYPTION_KEY (same as secrets)
    private_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    public_key_pem: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[created_at]
