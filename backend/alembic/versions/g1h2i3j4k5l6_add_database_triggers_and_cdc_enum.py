"""add database_triggers table and CDC trigger type

Revision ID: g1h2i3j4k5l6
Revises: fe05de67fg89, e6f7a8b9c0d1
Create Date: 2026-03-02
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "g1h2i3j4k5l6"
down_revision = ("fe05de67fg89", "e6f7a8b9c0d1")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add CDC to TriggerType enum
    op.execute("ALTER TYPE triggertype ADD VALUE IF NOT EXISTS 'CDC'")

    # Create database_triggers table
    op.create_table(
        "database_triggers",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False, index=True),
        sa.Column("database_connection_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("database_connections.id"), nullable=False, index=True),
        sa.Column("schema_name", sa.String(255), nullable=False, server_default="public"),
        sa.Column("table_name", sa.String(255), nullable=False),
        sa.Column("operations", sa.JSON(), nullable=False),
        sa.Column("function_namespace", sa.String(255), nullable=False, server_default="default"),
        sa.Column("function_name", sa.String(255), nullable=False),
        sa.Column("poll_column", sa.String(255), nullable=False),
        sa.Column("poll_interval_seconds", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("batch_size", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("is_active", sa.Boolean(), server_default="true"),
        sa.Column("last_poll_value", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("managed_by", sa.Text(), nullable=True),
        sa.Column("config_name", sa.Text(), nullable=True),
        sa.Column("config_checksum", sa.Text(), nullable=True),
        sa.UniqueConstraint("user_id", "name", name="uix_db_trigger_user_name"),
    )


def downgrade() -> None:
    op.drop_table("database_triggers")
    # Note: Cannot remove enum values in PostgreSQL
