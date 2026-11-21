"""manifests.exposed_tools: the functions, queries and pipelines a manifest
serves to external agents through the switchboard (/mcp/{ns}/{name}).

Empty for every existing manifest: nothing is exposed until declared.

Revision ID: g1a2t3w4a5y6
Revises: r1e2t3r4y5f6
Create Date: 2026-11-19
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "g1a2t3w4a5y6"
down_revision = "r1e2t3r4y5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "manifests",
        sa.Column("exposed_tools", postgresql.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("manifests", "exposed_tools")
