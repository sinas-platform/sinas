"""pending_delegations -> pending_completions + expiry deadlines

The suspend-on-delegate checkpoint table becomes the checkpoint for ANY
deferred tool round (sub-agent delegation, ask_user human input, …). A
rename — not a new table — so rows suspended mid-deploy keep working:
their `pending` entries simply lack a "completer" key, which the code
defaults to "sub_agent".

Both pending tables gain a nullable `expires_at` deadline for the expiry
sweep; NULL (all existing rows) means "never expires", exactly the prior
behavior.

Revision ID: d1f2c3m4p5l6
Revises: t1a2p3r4v5l6
Create Date: 2026-09-02
"""
import sqlalchemy as sa
from alembic import op

revision = "d1f2c3m4p5l6"
down_revision = "t1a2p3r4v5l6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.rename_table("pending_delegations", "pending_completions")
    op.execute(
        "ALTER INDEX ix_pending_delegations_chat_id RENAME TO ix_pending_completions_chat_id"
    )
    op.execute(
        "ALTER INDEX ix_pending_delegations_user_id RENAME TO ix_pending_completions_user_id"
    )
    op.add_column(
        "pending_completions",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_pending_completions_expires_at", "pending_completions", ["expires_at"]
    )

    op.add_column(
        "pending_tool_approvals",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_pending_tool_approvals_expires_at", "pending_tool_approvals", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_pending_tool_approvals_expires_at", table_name="pending_tool_approvals")
    op.drop_column("pending_tool_approvals", "expires_at")

    op.drop_index("ix_pending_completions_expires_at", table_name="pending_completions")
    op.drop_column("pending_completions", "expires_at")
    op.execute(
        "ALTER INDEX ix_pending_completions_chat_id RENAME TO ix_pending_delegations_chat_id"
    )
    op.execute(
        "ALTER INDEX ix_pending_completions_user_id RENAME TO ix_pending_delegations_user_id"
    )
    op.rename_table("pending_completions", "pending_delegations")
