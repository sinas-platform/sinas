"""add table annotations

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-02-27 18:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c4d5e6f7a8b9"
down_revision = "b3c4d5e6f7a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "table_annotations",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "database_connection_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("database_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("schema_name", sa.String(63), nullable=False, server_default="public"),
        sa.Column("table_name", sa.String(63), nullable=False),
        sa.Column("column_name", sa.String(63), nullable=True),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "database_connection_id",
            "schema_name",
            "table_name",
            "column_name",
            name="uq_table_annotations_conn_schema_table_col",
        ),
    )
    # Functional unique index for rows where column_name IS NULL (table-level annotations)
    op.create_index(
        "ix_table_annotations_table_level",
        "table_annotations",
        ["database_connection_id", "schema_name", "table_name"],
        unique=True,
        postgresql_where=sa.text("column_name IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_table_annotations_table_level", table_name="table_annotations")
    op.drop_table("table_annotations")
