"""add agent is_default

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-02-19

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f5a6b7c8d9e0'
down_revision = 'e4f5a6b7c8d9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('agents', sa.Column('is_default', sa.Boolean(), server_default='false', nullable=False))


def downgrade() -> None:
    op.drop_column('agents', 'is_default')
