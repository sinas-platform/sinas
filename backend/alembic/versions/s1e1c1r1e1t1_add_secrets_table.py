"""add secrets table

Revision ID: s1e1c1r1e1t1
Revises: h1i2j3k4l5m6
Create Date: 2026-03-20
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "s1e1c1r1e1t1"
down_revision = "h1i2j3k4l5m6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "secrets",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("encrypted_value", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("managed_by", sa.Text, nullable=True),
        sa.Column("config_name", sa.Text, nullable=True),
        sa.Column("config_checksum", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("secrets")
