"""manifests.switchboard_limits: per-caller rate limit for the manifest's
switchboard tool calls ({"maxCalls": n, "windowSeconds": s}); NULL means the deployment
default (settings switchboard_rate_limit_*).

Revision ID: g2l3i4m5i6t7
Revises: g1a2t3w4a5y6
Create Date: 2026-11-20
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "g2l3i4m5i6t7"
down_revision = "g1a2t3w4a5y6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("manifests", sa.Column("switchboard_limits", postgresql.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("manifests", "switchboard_limits")
