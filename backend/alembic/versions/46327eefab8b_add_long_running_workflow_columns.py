"""add long-running workflow columns to agents and chats

Revision ID: 46327eefab8b
Revises: fa7464554e5f
Create Date: 2026-03-12 00:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = '46327eefab8b'
down_revision = 'fa7464554e5f'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Agent: long-running workflow defaults
    op.add_column('agents', sa.Column('default_job_timeout', sa.Integer(), nullable=True))
    op.add_column('agents', sa.Column('default_keep_alive', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('agents', sa.Column('enable_code_execution', sa.Boolean(), nullable=False, server_default='false'))

    # Chat: per-chat overrides + reconnection channel
    op.add_column('chats', sa.Column('job_timeout', sa.Integer(), nullable=True))
    op.add_column('chats', sa.Column('keep_alive', sa.Boolean(), nullable=True))
    op.add_column('chats', sa.Column('active_channel_id', sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column('chats', 'active_channel_id')
    op.drop_column('chats', 'keep_alive')
    op.drop_column('chats', 'job_timeout')

    op.drop_column('agents', 'enable_code_execution')
    op.drop_column('agents', 'default_keep_alive')
    op.drop_column('agents', 'default_job_timeout')
