"""create pending_tool_approvals table

Revision ID: f9a0b1c2d3e4
Revises: e8f9a2b3c4d5
Create Date: 2026-01-21 18:30:00.000000

"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "f9a0b1c2d3e4"
down_revision = "e8f9a2b3c4d5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create pending_tool_approvals table
    op.create_table(
        "pending_tool_approvals",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chat_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_call_id", sa.String(255), nullable=False),
        sa.Column("function_namespace", sa.String(255), nullable=False),
        sa.Column("function_name", sa.String(255), nullable=False),
        sa.Column("arguments", postgresql.JSON(astext_type=sa.Text()), nullable=False),
        sa.Column("all_tool_calls", postgresql.JSON(astext_type=sa.Text()), nullable=False),
        sa.Column("conversation_context", postgresql.JSON(astext_type=sa.Text()), nullable=False),
        sa.Column("approved", sa.Boolean(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )

    # Create indexes
    op.create_index("ix_pending_tool_approvals_chat_id", "pending_tool_approvals", ["chat_id"])
    op.create_index(
        "ix_pending_tool_approvals_message_id", "pending_tool_approvals", ["message_id"]
    )
    op.create_index("ix_pending_tool_approvals_user_id", "pending_tool_approvals", ["user_id"])
    op.create_index(
        "ix_pending_tool_approvals_tool_call_id",
        "pending_tool_approvals",
        ["tool_call_id"],
        unique=True,
    )


def downgrade() -> None:
    # Drop indexes
    op.drop_index("ix_pending_tool_approvals_tool_call_id", table_name="pending_tool_approvals")
    op.drop_index("ix_pending_tool_approvals_user_id", table_name="pending_tool_approvals")
    op.drop_index("ix_pending_tool_approvals_message_id", table_name="pending_tool_approvals")
    op.drop_index("ix_pending_tool_approvals_chat_id", table_name="pending_tool_approvals")

    # Drop table
    op.drop_table("pending_tool_approvals")
