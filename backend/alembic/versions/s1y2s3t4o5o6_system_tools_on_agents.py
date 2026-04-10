"""Replace enable_code_execution with system_tools list on agents

Revision ID: s1y2s3t4o5o6
Revises: f1i2x3u4p5d6
Create Date: 2026-04-07
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "s1y2s3t4o5o6"
down_revision = "f1i2x3u4p5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add new column
    op.add_column(
        "agents",
        sa.Column(
            "system_tools",
            postgresql.JSON(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )

    # 2. Migrate data: enable_code_execution=true -> system_tools=["codeExecution"]
    op.execute(
        """
        UPDATE agents
        SET system_tools = '["codeExecution"]'::json
        WHERE enable_code_execution = true
        """
    )

    # 3. Drop the old column
    op.drop_column("agents", "enable_code_execution")


def downgrade() -> None:
    # 1. Re-add the old column
    op.add_column(
        "agents",
        sa.Column(
            "enable_code_execution",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # 2. Migrate data back
    op.execute(
        """
        UPDATE agents
        SET enable_code_execution = true
        WHERE system_tools::jsonb ? 'codeExecution'
        """
    )

    # 3. Drop the new column
    op.drop_column("agents", "system_tools")
