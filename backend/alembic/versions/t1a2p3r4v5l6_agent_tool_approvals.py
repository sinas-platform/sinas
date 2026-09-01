"""agents.tool_approvals: per-agent tool approval rules (permission modes)

Revision ID: t1a2p3r4v5l6
Revises: w1o2r3k4b5n6
Create Date: 2026-09-01
"""
import sqlalchemy as sa
from alembic import op

revision = "t1a2p3r4v5l6"
down_revision = "w1o2r3k4b5n6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable: NULL keeps today's behavior (function requires_approval only).
    op.add_column("agents", sa.Column("tool_approvals", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("agents", "tool_approvals")
