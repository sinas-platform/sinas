"""add collection config tracking fields

Revision ID: c2d3e4f5a6b7
Revises: bf3780240859
Create Date: 2026-02-16 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c2d3e4f5a6b7'
down_revision = 'bf3780240859'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('collections', sa.Column('managed_by', sa.Text(), nullable=True))
    op.add_column('collections', sa.Column('config_name', sa.Text(), nullable=True))
    op.add_column('collections', sa.Column('config_checksum', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('collections', 'config_checksum')
    op.drop_column('collections', 'config_name')
    op.drop_column('collections', 'managed_by')
