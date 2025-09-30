from sqlalchemy import String, Text, Boolean, JSON, Enum, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from typing import Optional, Dict, Any
import enum

from .base import Base, uuid_pk, created_at, updated_at


class HTTPMethod(str, enum.Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    DELETE = "DELETE"
    PATCH = "PATCH"


class Webhook(Base):
    __tablename__ = "webhooks"
    __table_args__ = (
        UniqueConstraint('path', 'http_method', name='uix_webhook_path_method'),
    )

    id: Mapped[uuid_pk]
    subtenant_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    path: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    function_name: Mapped[str] = mapped_column(String(255), nullable=False)
    http_method: Mapped[HTTPMethod] = mapped_column(
        Enum(HTTPMethod), default=HTTPMethod.POST, nullable=False
    )
    description: Mapped[Optional[str]] = mapped_column(Text)
    default_values: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    requires_auth: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]