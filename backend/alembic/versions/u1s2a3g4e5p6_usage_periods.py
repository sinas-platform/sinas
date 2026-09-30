"""usage_periods table for operations metering

Revision ID: u1s2a3g4e5p6
Revises: o1i2d3c4k5y6
Create Date: 2026-08-10
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = "u1s2a3g4e5p6"
down_revision = "o1i2d3c4k5y6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "usage_periods",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("instance_id", sa.String(255), nullable=False),
        sa.Column("period_id", sa.String(20), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("by_kind", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("last_op_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snapshot_seq", sa.Integer, nullable=False, server_default="0"),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "uq_usage_periods_instance_period",
        "usage_periods",
        ["instance_id", "period_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_usage_periods_instance_period", table_name="usage_periods")
    op.drop_table("usage_periods")
