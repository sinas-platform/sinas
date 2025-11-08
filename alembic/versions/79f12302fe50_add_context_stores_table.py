"""add_context_stores_table

Revision ID: 79f12302fe50
Revises: db82cc8a2e04
Create Date: 2025-11-07 12:37:56.345689

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '79f12302fe50'
down_revision = 'db82cc8a2e04'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create context_stores table
    op.create_table('context_stores',
        sa.Column('id', postgresql.UUID(), nullable=False),
        sa.Column('user_id', postgresql.UUID(), nullable=False),
        sa.Column('group_id', postgresql.UUID(), nullable=True),
        sa.Column('assistant_id', postgresql.UUID(), nullable=True),
        sa.Column('namespace', sa.String(length=100), nullable=False),
        sa.Column('key', sa.String(length=255), nullable=False),
        sa.Column('value', sa.JSON(), nullable=False),
        sa.Column('visibility', sa.String(length=20), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('tags', sa.JSON(), nullable=False),
        sa.Column('relevance_score', sa.Float(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['assistant_id'], ['assistants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )

    # Create indexes
    op.create_index(op.f('ix_context_stores_user_id'), 'context_stores', ['user_id'], unique=False)
    op.create_index(op.f('ix_context_stores_group_id'), 'context_stores', ['group_id'], unique=False)
    op.create_index(op.f('ix_context_stores_assistant_id'), 'context_stores', ['assistant_id'], unique=False)
    op.create_index(op.f('ix_context_stores_namespace'), 'context_stores', ['namespace'], unique=False)
    op.create_index(op.f('ix_context_stores_key'), 'context_stores', ['key'], unique=False)
    op.create_index(op.f('ix_context_stores_visibility'), 'context_stores', ['visibility'], unique=False)
    op.create_index('ix_context_stores_namespace_visibility', 'context_stores', ['namespace', 'visibility'], unique=False)
    op.create_index('ix_context_stores_expires_at', 'context_stores', ['expires_at'], unique=False)

    # Create unique constraint for user_id + namespace + key
    op.create_index('uq_context_store_user_namespace_key', 'context_stores', ['user_id', 'namespace', 'key'], unique=True)


def downgrade() -> None:
    # Drop unique constraint
    op.drop_index('uq_context_store_user_namespace_key', table_name='context_stores')

    # Drop indexes
    op.drop_index('ix_context_stores_expires_at', table_name='context_stores')
    op.drop_index('ix_context_stores_namespace_visibility', table_name='context_stores')
    op.drop_index(op.f('ix_context_stores_visibility'), table_name='context_stores')
    op.drop_index(op.f('ix_context_stores_key'), table_name='context_stores')
    op.drop_index(op.f('ix_context_stores_namespace'), table_name='context_stores')
    op.drop_index(op.f('ix_context_stores_assistant_id'), table_name='context_stores')
    op.drop_index(op.f('ix_context_stores_group_id'), table_name='context_stores')
    op.drop_index(op.f('ix_context_stores_user_id'), table_name='context_stores')

    # Drop table
    op.drop_table('context_stores')