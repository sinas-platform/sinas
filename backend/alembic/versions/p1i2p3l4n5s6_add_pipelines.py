"""add pipelines, pipeline_runs, pipeline_cursors; PIPELINE trigger type;
agents.enabled_pipelines; database_triggers pipeline targets

Revision ID: p1i2p3l4n5s6
Revises: w2a3b4t5g6t7
Create Date: 2026-07-28
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "p1i2p3l4n5s6"
down_revision = "w2a3b4t5g6t7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # New TriggerType value for child executions of pipeline runs
    op.execute("ALTER TYPE triggertype ADD VALUE IF NOT EXISTS 'PIPELINE'")

    op.create_table(
        "pipelines",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("namespace", sa.String(255), nullable=False, server_default="default", index=True),
        sa.Column("name", sa.String(255), nullable=False, index=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("input_schema", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("steps", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("per_user", sa.JSON(), nullable=True),
        sa.Column("as_tool", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("tool_description", sa.Text(), nullable=True),
        sa.Column("sync_timeout_seconds", sa.Integer(), nullable=False, server_default="120"),
        sa.Column("concurrency", sa.String(10), nullable=True),
        sa.Column("disable_after_failures", sa.Integer(), nullable=True),
        sa.Column("output_mapping", sa.JSON(), nullable=True),
        sa.Column("cursor_value", sa.Text(), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("managed_by", sa.Text(), nullable=True),
        sa.Column("config_name", sa.Text(), nullable=True),
        sa.Column("config_checksum", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("namespace", "name", name="uq_pipeline_namespace_name"),
    )

    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "pipeline_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pipelines.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("run_id", sa.String(255), nullable=False, unique=True, index=True),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "trigger_type",
            sa.dialects.postgresql.ENUM(name="triggertype", create_type=False),
            nullable=False,
        ),
        sa.Column("trigger_id", sa.String(255), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="running", index=True),
        sa.Column("input", sa.JSON(), nullable=True),
        sa.Column("steps", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("cursor_before", sa.Text(), nullable=True),
        sa.Column("cursor_after", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
    )

    op.create_table(
        "pipeline_cursors",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "pipeline_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pipelines.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("cursor_value", sa.Text(), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("pipeline_id", "user_id", name="uq_pipeline_cursor_pipeline_user"),
    )

    # Agents can expose asTool pipelines
    op.add_column(
        "agents",
        sa.Column("enabled_pipelines", sa.JSON(), nullable=False, server_default="[]"),
    )

    # Webhooks gain a pipeline target (third target_type value, after #111's "agent")
    op.add_column("webhooks", sa.Column("pipeline_namespace", sa.String(255), nullable=True))
    op.add_column("webhooks", sa.Column("pipeline_name", sa.String(255), nullable=True))

    # Database triggers (CDC) gain a pipeline target
    op.add_column(
        "database_triggers",
        sa.Column("target_type", sa.String(10), nullable=False, server_default="function"),
    )
    op.add_column("database_triggers", sa.Column("pipeline_namespace", sa.String(255), nullable=True))
    op.add_column("database_triggers", sa.Column("pipeline_name", sa.String(255), nullable=True))
    # function_name is no longer required (pipeline-target triggers have no function)
    op.alter_column("database_triggers", "function_name", existing_type=sa.String(255), nullable=True)


def downgrade() -> None:
    op.drop_column("webhooks", "pipeline_name")
    op.drop_column("webhooks", "pipeline_namespace")
    op.alter_column("database_triggers", "function_name", existing_type=sa.String(255), nullable=False)
    op.drop_column("database_triggers", "pipeline_name")
    op.drop_column("database_triggers", "pipeline_namespace")
    op.drop_column("database_triggers", "target_type")
    op.drop_column("agents", "enabled_pipelines")
    op.drop_table("pipeline_cursors")
    op.drop_table("pipeline_runs")
    op.drop_table("pipelines")
    # Note: Cannot remove enum values in PostgreSQL
