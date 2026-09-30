"""persist pipeline run output

Revision ID: r1u2n3o4u5t6
Revises: a1g2t3o4v5r6
Create Date: 2026-08-21
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "r1u2n3o4u5t6"
down_revision = "a1g2t3o4v5r6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pipeline_runs", sa.Column("output", sa.JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("pipeline_runs", "output")
