"""mcp_servers table + agents.enabled_mcp_servers

A remote Model Context Protocol server becomes a resource agents can enable
as a tool source, beside connectors. Purely additive: a new table, and one
JSON column on agents with a server default of [] so existing rows and API
shapes are untouched.

Revision ID: m1c2p3s4r5v6
Revises: d1f2c3m4p5l6
Create Date: 2026-10-09
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "m1c2p3s4r5v6"
down_revision = "t1a2p3r4v5l6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_servers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("namespace", sa.String(length=100), nullable=False, server_default="default"),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("transport", sa.String(length=32), nullable=False, server_default="streamable_http"),
        sa.Column("auth", sa.JSON(), nullable=False, server_default='{"type": "none"}'),
        sa.Column("headers", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("tool_allow", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("tool_deny", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("connect_timeout_seconds", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("managed_by", sa.Text(), nullable=True),
        sa.Column("config_name", sa.Text(), nullable=True),
        sa.Column("config_checksum", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("namespace", "name", name="uq_mcp_server_namespace_name"),
    )
    op.create_index("ix_mcp_servers_user_id", "mcp_servers", ["user_id"])
    op.create_index("ix_mcp_servers_namespace", "mcp_servers", ["namespace"])
    op.create_index("ix_mcp_servers_name", "mcp_servers", ["name"])

    op.add_column(
        "agents",
        sa.Column("enabled_mcp_servers", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("agents", "enabled_mcp_servers")
    op.drop_index("ix_mcp_servers_name", table_name="mcp_servers")
    op.drop_index("ix_mcp_servers_namespace", table_name="mcp_servers")
    op.drop_index("ix_mcp_servers_user_id", table_name="mcp_servers")
    op.drop_table("mcp_servers")
