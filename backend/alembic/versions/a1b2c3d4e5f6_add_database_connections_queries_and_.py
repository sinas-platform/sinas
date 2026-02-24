"""add database_connections, queries tables and agent query fields

Revision ID: a1b2c3d4e5f6
Revises: f9a0b1c2d3e4, ebe0ac802f33
Create Date: 2026-02-24 12:00:00.000000

"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = ("f9a0b1c2d3e4", "ebe0ac802f33")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create database_connections table
    op.create_table(
        "database_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("connection_type", sa.String(50), nullable=False),
        sa.Column("host", sa.String(500), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("database", sa.String(255), nullable=False),
        sa.Column("username", sa.String(255), nullable=False),
        sa.Column("password", sa.Text(), nullable=True),
        sa.Column("ssl_mode", sa.String(50), nullable=True),
        sa.Column("config", postgresql.JSON(astext_type=sa.Text()), nullable=True, server_default="{}"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("managed_by", sa.Text(), nullable=True),
        sa.Column("config_name", sa.Text(), nullable=True),
        sa.Column("config_checksum", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    # Create queries table
    op.create_table(
        "queries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("namespace", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("database_connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation", sa.String(10), nullable=False),
        sa.Column("sql", sa.Text(), nullable=False),
        sa.Column("input_schema", postgresql.JSON(astext_type=sa.Text()), nullable=True, server_default="{}"),
        sa.Column("output_schema", postgresql.JSON(astext_type=sa.Text()), nullable=True, server_default="{}"),
        sa.Column("timeout_ms", sa.Integer(), nullable=True, server_default="5000"),
        sa.Column("max_rows", sa.Integer(), nullable=True, server_default="1000"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("managed_by", sa.Text(), nullable=True),
        sa.Column("config_name", sa.Text(), nullable=True),
        sa.Column("config_checksum", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["database_connection_id"], ["database_connections.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("namespace", "name", name="uq_query_namespace_name"),
    )
    op.create_index(op.f("ix_queries_namespace"), "queries", ["namespace"])
    op.create_index(op.f("ix_queries_name"), "queries", ["name"])
    op.create_index(op.f("ix_queries_user_id"), "queries", ["user_id"])
    op.create_index(op.f("ix_queries_database_connection_id"), "queries", ["database_connection_id"])

    # Add query fields to agents table
    op.add_column("agents", sa.Column("enabled_queries", postgresql.JSON(astext_type=sa.Text()), nullable=True, server_default="[]"))
    op.add_column("agents", sa.Column("query_parameters", postgresql.JSON(astext_type=sa.Text()), nullable=True, server_default="{}"))


def downgrade() -> None:
    op.drop_column("agents", "query_parameters")
    op.drop_column("agents", "enabled_queries")
    op.drop_index(op.f("ix_queries_database_connection_id"), table_name="queries")
    op.drop_index(op.f("ix_queries_user_id"), table_name="queries")
    op.drop_index(op.f("ix_queries_name"), table_name="queries")
    op.drop_index(op.f("ix_queries_namespace"), table_name="queries")
    op.drop_table("queries")
    op.drop_table("database_connections")
