"""component_shares: access mode (snapshot / viewer / creator) and allow_writes

Revision ID: s1h2a3r4e5m6
Revises: h1t2m3l4c5p6
Create Date: 2026-10-08
"""

import sqlalchemy as sa

from alembic import op

revision = "s1h2a3r4e5m6"
down_revision = "h1t2m3l4c5p6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing links keep what they did: fixed inputs, no live access.
    op.add_column(
        "component_shares",
        sa.Column("mode", sa.String(20), nullable=False, server_default="snapshot"),
    )
    op.add_column(
        "component_shares",
        sa.Column("allow_writes", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("component_shares", "allow_writes")
    op.drop_column("component_shares", "mode")
