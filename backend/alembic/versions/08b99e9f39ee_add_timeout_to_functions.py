"""add timeout to functions

Revision ID: 08b99e9f39ee
Revises: 7b66e1c1642d
Create Date: 2026-03-10 12:12:19.384084

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '08b99e9f39ee'
down_revision = '7b66e1c1642d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('functions', sa.Column('timeout', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('functions', 'timeout')
