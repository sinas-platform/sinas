"""add connectors table and enabled_connectors to agents

Revision ID: c0n1n2e3c4t5
Revises: s1e1c1r1e1t1
Create Date: 2026-03-20
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c0n1n2e3c4t5"
down_revision = "s1e1c1r1e1t1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "connectors",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("namespace", sa.String(100), nullable=False, index=True, server_default="default"),
        sa.Column("name", sa.String(100), nullable=False, index=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("base_url", sa.Text, nullable=False),
        sa.Column("auth", sa.JSON, nullable=False, server_default='{"type": "none"}'),
        sa.Column("headers", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("retry", sa.JSON, nullable=False, server_default='{"max_attempts": 1, "backoff": "none"}'),
        sa.Column("timeout_seconds", sa.Integer, nullable=False, server_default="30"),
        sa.Column("operations", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("managed_by", sa.Text, nullable=True),
        sa.Column("config_name", sa.Text, nullable=True),
        sa.Column("config_checksum", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("namespace", "name", name="uq_connector_namespace_name"),
    )

    op.add_column(
        "agents",
        sa.Column("enabled_connectors", sa.JSON, nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("agents", "enabled_connectors")
    op.drop_table("connectors")
