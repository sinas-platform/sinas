from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column
from typing import Optional

from .base import Base, uuid_pk, created_at


class Subtenant(Base):
    __tablename__ = "subtenants"

    id: Mapped[uuid_pk]  # This IS the subtenant_id
    description: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[created_at]