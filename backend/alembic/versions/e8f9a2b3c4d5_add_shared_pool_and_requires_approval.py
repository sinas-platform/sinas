"""add shared_pool and requires_approval to functions

Revision ID: e8f9a2b3c4d5
Revises: 734db91ebacb
Create Date: 2026-01-21 18:00:00.000000

"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "e8f9a2b3c4d5"
down_revision = "734db91ebacb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add shared_pool column (default False for safety - isolated containers)
    op.add_column(
        "functions", sa.Column("shared_pool", sa.Boolean(), nullable=False, server_default="false")
    )

    # Add requires_approval column (default False for convenience)
    op.add_column(
        "functions",
        sa.Column("requires_approval", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("functions", "requires_approval")
    op.drop_column("functions", "shared_pool")
