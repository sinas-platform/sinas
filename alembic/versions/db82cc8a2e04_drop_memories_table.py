"""drop_memories_table

Revision ID: db82cc8a2e04
Revises: a3ba17081d99
Create Date: 2025-11-06 17:33:44.618273

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'db82cc8a2e04'
down_revision = 'a3ba17081d99'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop indices first
    op.drop_index(op.f('ix_memories_user_id'), table_name='memories')
    op.drop_index(op.f('ix_memories_group_id'), table_name='memories')

    # Drop the memories table
    op.drop_table('memories')


def downgrade() -> None:
    # Recreate the memories table for rollback safety
    op.create_table('memories',
        sa.Column('id', postgresql.UUID(), nullable=False),
        sa.Column('user_id', postgresql.UUID(), nullable=False),
        sa.Column('group_id', postgresql.UUID(), nullable=True),
        sa.Column('key', sa.String(length=255), nullable=False),
        sa.Column('value', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    # Recreate the indices
    op.create_index(op.f('ix_memories_group_id'), 'memories', ['group_id'], unique=False)
    op.create_index(op.f('ix_memories_user_id'), 'memories', ['user_id'], unique=False)