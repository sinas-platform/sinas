"""add session_key to chats

Revision ID: i1n2v3o4k5e6
Revises: c0n1n2e3c4t5
Create Date: 2026-03-20
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "i1n2v3o4k5e6"
down_revision = "c0n1n2e3c4t5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chats", sa.Column("session_key", sa.String(500), nullable=True))
    op.create_index(
        "ix_chats_session_key",
        "chats",
        ["session_key"],
    )
    op.create_index(
        "uq_chat_agent_session_key",
        "chats",
        ["agent_id", "session_key"],
        unique=True,
        postgresql_where=sa.text("session_key IS NOT NULL AND archived = false"),
    )


def downgrade() -> None:
    op.drop_index("uq_chat_agent_session_key", table_name="chats")
    op.drop_index("ix_chats_session_key", table_name="chats")
    op.drop_column("chats", "session_key")
