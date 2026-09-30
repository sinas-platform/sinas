"""add provider-native batch mode columns to batches

Revision ID: p1r2o3v4b5t6
Revises: u1s2a3g4e5p6
Create Date: 2026-08-10
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "p1r2o3v4b5t6"
down_revision = "u1s2a3g4e5p6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "batches",
        sa.Column("execution_mode", sa.String(20), nullable=False, server_default="queue"),
    )
    op.add_column(
        "batches",
        sa.Column("provider_batch_id", sa.String(255), nullable=True),
    )
    op.add_column(
        "batches",
        sa.Column("llm_provider_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("batches", "llm_provider_id")
    op.drop_column("batches", "provider_batch_id")
    op.drop_column("batches", "execution_mode")
