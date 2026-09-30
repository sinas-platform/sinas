"""agent.provider_overrides — per-agent whitelisted provider settings

Revision ID: a1g2t3o4v5r6
Revises: p1r2o3v4b5t6
Create Date: 2026-08-11
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a1g2t3o4v5r6"
down_revision = "p1r2o3v4b5t6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("provider_overrides", sa.JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("agents", "provider_overrides")
