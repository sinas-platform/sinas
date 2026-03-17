"""add attempts column to otp_sessions

Revision ID: h1i2j3k4l5m6
Revises: 72b8c42ea2f2
Create Date: 2026-03-16
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "h1i2j3k4l5m6"
down_revision = "72b8c42ea2f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("otp_sessions", sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("otp_sessions", "attempts")
