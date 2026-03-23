"""add hooks to agents

Revision ID: h1o2o3k4s5a6
Revises: i1n2v3o4k5e6
Create Date: 2026-03-20
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "h1o2o3k4s5a6"
down_revision = "i1n2v3o4k5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("hooks", sa.JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("agents", "hooks")
