"""unify schedule target fields

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-02-19

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e4f5a6b7c8d9'
down_revision = 'd3e4f5a6b7c8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column('scheduled_jobs', 'function_namespace', new_column_name='target_namespace')
    op.alter_column('scheduled_jobs', 'function_name', new_column_name='target_name')
    op.add_column('scheduled_jobs', sa.Column('schedule_type', sa.String(20), server_default='function', nullable=False))
    op.add_column('scheduled_jobs', sa.Column('content', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('scheduled_jobs', 'content')
    op.drop_column('scheduled_jobs', 'schedule_type')
    op.alter_column('scheduled_jobs', 'target_name', new_column_name='function_name')
    op.alter_column('scheduled_jobs', 'target_namespace', new_column_name='function_namespace')
