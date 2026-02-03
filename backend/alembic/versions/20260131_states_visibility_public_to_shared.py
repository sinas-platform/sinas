"""states visibility public to shared

Revision ID: 20260131_states
Revises: 4ab62487e5b9
Create Date: 2026-01-31 11:00:00.000000

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260131_states"
down_revision = "4ab62487e5b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Update states table visibility values:
    - 'public' -> 'shared'
    - 'group' -> 'private' (group feature was broken/unused)
    """
    # Update any existing 'public' visibility to 'shared'
    op.execute("UPDATE states SET visibility = 'shared' WHERE visibility = 'public'")

    # Update any existing 'group' visibility to 'private' (group feature was broken)
    op.execute("UPDATE states SET visibility = 'private' WHERE visibility = 'group'")


def downgrade() -> None:
    """Reverse the migration."""
    # Reverse: 'shared' -> 'public'
    op.execute("UPDATE states SET visibility = 'public' WHERE visibility = 'shared'")
