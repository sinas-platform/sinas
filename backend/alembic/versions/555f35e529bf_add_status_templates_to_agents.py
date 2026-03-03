"""add status_templates to agents

Revision ID: 555f35e529bf
Revises: 89a6f782460e
Create Date: 2026-03-03 17:00:11.514563

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '555f35e529bf'
down_revision = '89a6f782460e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('agents', sa.Column('status_templates', sa.JSON(), server_default='{}', nullable=False))


def downgrade() -> None:
    op.drop_column('agents', 'status_templates')
