"""add response_mode and dedup to webhooks

Revision ID: w1e2b3h4o5k6
Revises: t1o2o3l4r5s6
Create Date: 2026-03-26
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "w1e2b3h4o5k6"
down_revision = "t1o2o3l4r5s6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("webhooks", sa.Column("response_mode", sa.String(10), nullable=False, server_default="sync"))
    op.add_column("webhooks", sa.Column("dedup", sa.JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("webhooks", "dedup")
    op.drop_column("webhooks", "response_mode")
