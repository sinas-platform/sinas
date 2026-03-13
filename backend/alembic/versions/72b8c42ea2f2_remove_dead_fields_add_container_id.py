"""Remove dead fields (requirements, enabled_namespaces, generator_state), add container_id

Revision ID: 72b8c42ea2f2
Revises: 46327eefab8b
Create Date: 2026-03-13 00:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = '72b8c42ea2f2'
down_revision = '46327eefab8b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop dead columns from functions
    op.drop_column('functions', 'requirements')
    op.drop_column('functions', 'enabled_namespaces')

    # Drop generator_state from executions (old dill-based pause/resume)
    op.drop_column('executions', 'generator_state')

    # Add container_id to executions (tracks which container holds a paused execution)
    op.add_column('executions', sa.Column('container_id', sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column('executions', 'container_id')
    op.add_column('executions', sa.Column('generator_state', sa.LargeBinary(), nullable=True))
    op.add_column('functions', sa.Column('enabled_namespaces', sa.JSON(), nullable=True, server_default='[]'))
    op.add_column('functions', sa.Column('requirements', sa.JSON(), nullable=True, server_default='[]'))
