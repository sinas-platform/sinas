"""add_context_namespaces_to_assistant

Revision ID: d997028a998e
Revises: 79f12302fe50
Create Date: 2025-11-07 13:00:06.221576

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd997028a998e'
down_revision = '79f12302fe50'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add context_namespaces column to assistants table
    op.add_column('assistants', sa.Column('context_namespaces', sa.JSON(), nullable=True))


def downgrade() -> None:
    # Remove context_namespaces column from assistants table
    op.drop_column('assistants', 'context_namespaces')