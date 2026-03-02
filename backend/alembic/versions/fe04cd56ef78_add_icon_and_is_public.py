"""add icon to agents/functions and is_public to collections

Revision ID: fe04cd56ef78
Revises: fe03cd45ef67
Create Date: 2026-03-01
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "fe04cd56ef78"
down_revision = "fe03cd45ef67"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("icon", sa.String(512), nullable=True))
    op.add_column("functions", sa.Column("icon", sa.String(512), nullable=True))
    op.add_column(
        "collections",
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("collections", "is_public")
    op.drop_column("functions", "icon")
    op.drop_column("agents", "icon")
