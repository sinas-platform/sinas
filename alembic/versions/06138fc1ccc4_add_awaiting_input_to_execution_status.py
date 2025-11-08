"""add_awaiting_input_to_execution_status

Revision ID: 06138fc1ccc4
Revises: 4ae78c37abf2
Create Date: 2025-11-07 14:38:13.535780

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '06138fc1ccc4'
down_revision = '4ae78c37abf2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add AWAITING_INPUT value to executionstatus enum (uppercase to match existing values)
    op.execute("ALTER TYPE executionstatus ADD VALUE IF NOT EXISTS 'AWAITING_INPUT'")


def downgrade() -> None:
    # Note: PostgreSQL doesn't support removing enum values directly
    # Would require recreating the enum type, which is complex with existing data
    pass