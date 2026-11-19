"""chat_provider_credentials: a chat's own LLM provider credential per provider

Per-run provider credentials (app/providers/run_credentials.py): encrypted,
bound to a chat by provider name, deleted with the chat. llm_usage records
whether a call presented one (never the value).

Revision ID: r2u3n4c5r6d7
Revises: r1e2t3r4y5f6
Create Date: 2026-11-19
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "r2u3n4c5r6d7"
down_revision = "r1e2t3r4y5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_provider_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "chat_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chats.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider_name", sa.String(100), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "set_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("chat_id", "provider_name", name="uq_chat_provider_credentials_chat_provider"),
    )
    op.create_index(
        "ix_chat_provider_credentials_chat_id", "chat_provider_credentials", ["chat_id"]
    )
    op.create_index(
        "ix_chat_provider_credentials_expires_at", "chat_provider_credentials", ["expires_at"]
    )
    op.add_column(
        "llm_usage",
        sa.Column("run_credential", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("llm_usage", "run_credential")
    op.drop_index("ix_chat_provider_credentials_expires_at", table_name="chat_provider_credentials")
    op.drop_index("ix_chat_provider_credentials_chat_id", table_name="chat_provider_credentials")
    op.drop_table("chat_provider_credentials")
