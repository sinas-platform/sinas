"""add_agent_trigger_type

Revision ID: 734db91ebacb
Revises: 4887dfb7d6ac
Create Date: 2026-01-16 15:55:05.215323

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "734db91ebacb"
down_revision = "4887dfb7d6ac"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add 'AGENT' to the triggertype enum (uppercase to match WEBHOOK and SCHEDULE)
    # Note: ALTER TYPE ADD VALUE cannot run inside a transaction block
    op.execute("COMMIT")  # End any existing transaction
    op.execute("ALTER TYPE triggertype ADD VALUE IF NOT EXISTS 'AGENT'")


def downgrade() -> None:
    # PostgreSQL doesn't support removing enum values directly
    # This would require recreating the enum type, which is complex and risky
    # Instead, we'll leave the enum value in place
    pass
