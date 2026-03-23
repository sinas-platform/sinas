"""add HOOK to triggertype enum

Revision ID: h1o2o3k4s5a7
Revises: h1o2o3k4s5a6
Create Date: 2026-03-20
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "h1o2o3k4s5a7"
down_revision = "h1o2o3k4s5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE triggertype ADD VALUE IF NOT EXISTS 'HOOK'")


def downgrade() -> None:
    # PostgreSQL doesn't support removing enum values
    pass
