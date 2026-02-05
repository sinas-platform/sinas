"""add_manual_trigger_type

Revision ID: a1c10336abd5
Revises: 290b7b865281
Create Date: 2026-02-05 17:50:43.359173

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1c10336abd5'
down_revision = '290b7b865281'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add 'MANUAL' to the triggertype enum
    # Note: ALTER TYPE ADD VALUE cannot run inside a transaction block
    op.execute("COMMIT")  # End any existing transaction
    op.execute("ALTER TYPE triggertype ADD VALUE IF NOT EXISTS 'MANUAL'")


def downgrade() -> None:
    # PostgreSQL doesn't support removing enum values directly
    # This would require recreating the enum type, which is complex and risky
    # Instead, we'll leave the enum value in place
    pass