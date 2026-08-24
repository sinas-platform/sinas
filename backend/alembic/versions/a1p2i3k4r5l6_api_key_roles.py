"""api_key_roles link table (API keys scoped by roles)

Revision ID: a1p2i3k4r5l6
Revises: r1u2n3o4u5t6
Create Date: 2026-08-24
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a1p2i3k4r5l6"
down_revision = "r1u2n3o4u5t6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_key_roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "api_key_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("api_keys.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("roles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_api_key_roles_api_key_id", "api_key_roles", ["api_key_id"])
    op.create_index("ix_api_key_roles_role_id", "api_key_roles", ["role_id"])
    op.create_index(
        "ix_api_key_role_unique", "api_key_roles", ["api_key_id", "role_id"], unique=True
    )


def downgrade() -> None:
    op.drop_table("api_key_roles")
