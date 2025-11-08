"""add_stateful_execution_fields

Revision ID: 4ae78c37abf2
Revises: d61d4c243a1f
Create Date: 2025-11-07 14:37:15.989087

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '4ae78c37abf2'
down_revision = 'd61d4c243a1f'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add stateful execution fields to executions table
    op.add_column('executions', sa.Column('generator_state', sa.LargeBinary(), nullable=True))
    op.add_column('executions', sa.Column('input_prompt', sa.Text(), nullable=True))
    op.add_column('executions', sa.Column('input_schema', sa.JSON(), nullable=True))


def downgrade() -> None:
    # Remove stateful execution fields from executions table
    op.drop_column('executions', 'input_schema')
    op.drop_column('executions', 'input_prompt')
    op.drop_column('executions', 'generator_state')