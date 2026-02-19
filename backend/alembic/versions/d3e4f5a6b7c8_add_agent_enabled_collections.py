"""add agent enabled_collections

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-02-16

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd3e4f5a6b7c8'
down_revision = 'c2d3e4f5a6b7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('agents', sa.Column('enabled_collections', sa.JSON(), server_default='[]', nullable=False))


def downgrade() -> None:
    op.drop_column('agents', 'enabled_collections')
