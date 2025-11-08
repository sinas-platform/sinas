"""add_chat_id_to_executions

Revision ID: d61d4c243a1f
Revises: 4600f2668ffc
Create Date: 2025-11-07 14:32:33.738491

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd61d4c243a1f'
down_revision = '4600f2668ffc'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add chat_id column to executions table
    op.add_column('executions', sa.Column('chat_id', sa.UUID(), nullable=True))
    op.create_index(op.f('ix_executions_chat_id'), 'executions', ['chat_id'], unique=False)
    op.create_foreign_key('fk_executions_chat_id', 'executions', 'chats', ['chat_id'], ['id'])


def downgrade() -> None:
    # Remove chat_id column from executions table
    op.drop_constraint('fk_executions_chat_id', 'executions', type_='foreignkey')
    op.drop_index(op.f('ix_executions_chat_id'), table_name='executions')
    op.drop_column('executions', 'chat_id')