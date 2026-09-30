"""metering v2: platform-issued periods

Revision ID: m1e2t3v4p5d6
Revises: p1w2d3r4s5t6
Create Date: 2026-08-31
"""
import sqlalchemy as sa
from alembic import op

revision = "m1e2t3v4p5d6"
down_revision = "p1w2d3r4s5t6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "metering_platform_period",
        sa.Column("instance_id", sa.String(length=255), primary_key=True),
        sa.Column("period_id", sa.String(length=64), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    # Platform period ids are ULID-ish and not bounded to the "YYYY-MM" shape
    op.alter_column(
        "usage_periods", "period_id", type_=sa.String(length=64), existing_nullable=False
    )


def downgrade() -> None:
    op.alter_column(
        "usage_periods", "period_id", type_=sa.String(length=20), existing_nullable=False
    )
    op.drop_table("metering_platform_period")
