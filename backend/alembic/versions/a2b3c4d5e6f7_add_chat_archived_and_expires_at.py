"""add chat archived and expires_at

Revision ID: a2b3c4d5e6f7
Revises: 671d4db601a8
Create Date: 2026-02-26 12:00:00.000000

"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "a2b3c4d5e6f7"
down_revision = "671d4db601a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chats",
        sa.Column("archived", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column(
        "chats",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(op.f("ix_chats_archived"), "chats", ["archived"])
    op.create_index(op.f("ix_chats_expires_at"), "chats", ["expires_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_chats_expires_at"), table_name="chats")
    op.drop_index(op.f("ix_chats_archived"), table_name="chats")
    op.drop_column("chats", "expires_at")
    op.drop_column("chats", "archived")
