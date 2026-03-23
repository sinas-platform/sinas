"""add tool_call_id to executions, drop step_executions

Revision ID: e1x2e3c4t5r6
Revises: h1o2o3k4s5a7
Create Date: 2026-03-23
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "e1x2e3c4t5r6"
down_revision = "h1o2o3k4s5a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("executions", sa.Column("tool_call_id", sa.String(255), nullable=True))
    op.create_index("ix_executions_tool_call_id", "executions", ["tool_call_id"])
    op.drop_table("step_executions")


def downgrade() -> None:
    op.create_table(
        "step_executions",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("execution_id", sa.String(255), sa.ForeignKey("executions.execution_id"), nullable=False, index=True),
        sa.Column("function_name", sa.String(255), nullable=False),
        sa.Column("status", sa.Enum("PENDING", "RUNNING", "AWAITING_INPUT", "COMPLETED", "FAILED", "CANCELLED", name="executionstatus", create_type=False), nullable=False),
        sa.Column("input_data", sa.JSON, nullable=False),
        sa.Column("output_data", sa.JSON),
        sa.Column("error", sa.Text),
        sa.Column("duration_ms", sa.Integer),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.drop_index("ix_executions_tool_call_id", table_name="executions")
    op.drop_column("executions", "tool_call_id")
