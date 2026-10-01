"""config revisions: change history for configurable resources

Revision ID: c1r2e3v4i5s6
Revises: m1e2t3v4p5d6
Create Date: 2026-10-01
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c1r2e3v4i5s6"
down_revision = "m1e2t3v4p5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "config_revisions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("resource_kind", sa.String(length=64), nullable=False),
        sa.Column("resource_key", sa.String(length=512), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("spec", sa.JSON(), nullable=True),
        sa.Column("changes", sa.JSON(), nullable=True),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_email", sa.String(length=255), nullable=True),
        sa.Column("managed_by", sa.Text(), nullable=True),
        sa.Column("config_name", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_config_revisions_kind_key", "config_revisions", ["resource_kind", "resource_key"]
    )
    op.create_index("ix_config_revisions_resource_id", "config_revisions", ["resource_id"])


def downgrade() -> None:
    op.drop_index("ix_config_revisions_resource_id", table_name="config_revisions")
    op.drop_index("ix_config_revisions_kind_key", table_name="config_revisions")
    op.drop_table("config_revisions")
