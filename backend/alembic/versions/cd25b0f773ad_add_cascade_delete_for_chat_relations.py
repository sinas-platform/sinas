"""add cascade delete for chat relations

Revision ID: cd25b0f773ad
Revises: 255094362009
Create Date: 2026-01-30 10:33:01.127464

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "cd25b0f773ad"
down_revision = "255094362009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Update messages.chat_id to CASCADE on delete
    op.drop_constraint("messages_chat_id_fkey", "messages", type_="foreignkey")
    op.create_foreign_key(
        "messages_chat_id_fkey", "messages", "chats", ["chat_id"], ["id"], ondelete="CASCADE"
    )

    # Update pending_tool_approvals.chat_id to CASCADE on delete
    op.drop_constraint(
        "pending_tool_approvals_chat_id_fkey", "pending_tool_approvals", type_="foreignkey"
    )
    op.create_foreign_key(
        "pending_tool_approvals_chat_id_fkey",
        "pending_tool_approvals",
        "chats",
        ["chat_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # Update executions.chat_id to SET NULL on delete
    op.drop_constraint("executions_chat_id_fkey", "executions", type_="foreignkey")
    op.create_foreign_key(
        "executions_chat_id_fkey", "executions", "chats", ["chat_id"], ["id"], ondelete="SET NULL"
    )


def downgrade() -> None:
    # Revert to no ondelete behavior
    op.drop_constraint("messages_chat_id_fkey", "messages", type_="foreignkey")
    op.create_foreign_key("messages_chat_id_fkey", "messages", "chats", ["chat_id"], ["id"])

    op.drop_constraint(
        "pending_tool_approvals_chat_id_fkey", "pending_tool_approvals", type_="foreignkey"
    )
    op.create_foreign_key(
        "pending_tool_approvals_chat_id_fkey",
        "pending_tool_approvals",
        "chats",
        ["chat_id"],
        ["id"],
    )

    op.drop_constraint("executions_chat_id_fkey", "executions", type_="foreignkey")
    op.create_foreign_key("executions_chat_id_fkey", "executions", "chats", ["chat_id"], ["id"])
