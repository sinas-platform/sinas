from sqlalchemy import String, Text, Boolean, Integer, JSON, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import Optional, List, Dict, Any
import uuid

from .base import Base, uuid_pk, created_at, updated_at


class Function(Base):
    __tablename__ = "functions"
    __table_args__ = (
        UniqueConstraint('subtenant_id', 'name', name='uix_function_subtenant_name'),
    )

    id: Mapped[uuid_pk]
    subtenant_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    input_schema: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    output_schema: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    requirements: Mapped[List[str]] = mapped_column(JSON, default=list)
    tags: Mapped[List[str]] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]

    versions: Mapped[List["FunctionVersion"]] = relationship(
        "FunctionVersion", back_populates="function", cascade="all, delete-orphan"
    )


class FunctionVersion(Base):
    __tablename__ = "function_versions"

    id: Mapped[uuid_pk]
    subtenant_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    function_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("functions.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    input_schema: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    output_schema: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[created_at]
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)

    function: Mapped["Function"] = relationship("Function", back_populates="versions")