"""fix enabled_components nullable on agents

Revision ID: 89a6f782460e
Revises: 148469d401ec
Create Date: 2026-03-02 13:26:35.284119

"""
from alembic import op
import sqlalchemy as sa


revision = '89a6f782460e'
down_revision = '148469d401ec'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("UPDATE agents SET enabled_components = '[]' WHERE enabled_components IS NULL"))
    op.alter_column('agents', 'enabled_components',
                    existing_type=sa.JSON(),
                    nullable=False,
                    server_default=sa.text("'[]'::json"))


def downgrade() -> None:
    op.alter_column('agents', 'enabled_components',
                    existing_type=sa.JSON(),
                    nullable=True,
                    server_default=None)
