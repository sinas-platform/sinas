"""add API to triggertype enum

Revision ID: 671d4db601a8
Revises: a1b2c3d4e5f6
Create Date: 2026-02-25 16:48:32.706526

"""
from alembic import op

# revision identifiers, used by Alembic.
revision = '671d4db601a8'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE triggertype ADD VALUE IF NOT EXISTS 'API'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values
    pass
