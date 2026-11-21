"""switchboard_calls: the audit log of tool calls made through the switchboard

One row per tools/call on POST /mcp/{ns}/{name}: who (user, API key), through
which manifest, which tool and resource, the redacted arguments, the clipped
result, outcome and duration. Kept for SWITCHBOARD_AUDIT_RETENTION_DAYS.

Revision ID: g3a4u5d6i7t8
Revises: g2l3i4m5i6t7
Create Date: 2026-11-20
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "g3a4u5d6i7t8"
down_revision = "g2l3i4m5i6t7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "switchboard_calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("manifest_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("manifests.id", ondelete="SET NULL"), nullable=True),
        sa.Column("manifest_namespace", sa.String(255), nullable=False),
        sa.Column("manifest_name", sa.String(255), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("api_key_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("tool_name", sa.String(64), nullable=False),
        sa.Column("resource_type", sa.String(16), nullable=False),
        sa.Column("resource_ref", sa.String(512), nullable=False),
        sa.Column("arguments", postgresql.JSONB(), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("is_error", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_switchboard_calls_manifest_created", "switchboard_calls", ["manifest_id", "created_at"])
    op.create_index("ix_switchboard_calls_user_created", "switchboard_calls", ["user_id", "created_at"])
    op.create_index("ix_switchboard_calls_api_key_id", "switchboard_calls", ["api_key_id"])
    op.create_index("ix_switchboard_calls_created_at", "switchboard_calls", ["created_at"])


def downgrade() -> None:
    for name in ("ix_switchboard_calls_created_at", "ix_switchboard_calls_api_key_id",
                 "ix_switchboard_calls_user_created", "ix_switchboard_calls_manifest_created"):
        op.drop_index(name, table_name="switchboard_calls")
    op.drop_table("switchboard_calls")
