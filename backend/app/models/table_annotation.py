"""Table annotation model for semantic layer (display names, descriptions)."""

from typing import Optional

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, created_at, updated_at, uuid_pk, GUID


class TableAnnotation(Base):
    """
    Stores optional display names and descriptions for tables and columns
    in external database connections.  column_name=NULL means table-level.
    """

    __tablename__ = "table_annotations"

    id: Mapped[uuid_pk]
    database_connection_id: Mapped[str] = mapped_column(
        GUID(),
        ForeignKey("database_connections.id", ondelete="CASCADE"),
        nullable=False,
    )
    schema_name: Mapped[str] = mapped_column(String(63), default="public", nullable=False)
    table_name: Mapped[str] = mapped_column(String(63), nullable=False)
    column_name: Mapped[Optional[str]] = mapped_column(String(63), nullable=True)

    display_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[created_at]
    updated_at: Mapped[updated_at]

    __table_args__ = (
        UniqueConstraint(
            "database_connection_id",
            "schema_name",
            "table_name",
            "column_name",
            name="uq_table_annotations_conn_schema_table_col",
        ),
    )
