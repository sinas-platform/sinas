"""jwt_signing_keys table for RS256 auto-generated keypairs (#101)

Revision ID: o1i2d3c4k5y6
Revises: p1i2p3l4n5s6
Create Date: 2026-08-10
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = "o1i2d3c4k5y6"
down_revision = "p1i2p3l4n5s6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jwt_signing_keys",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("purpose", sa.String(50), nullable=False, unique=True),
        sa.Column("algorithm", sa.String(10), nullable=False),
        sa.Column("kid", sa.String(100), nullable=False),
        sa.Column("private_key_encrypted", sa.Text, nullable=False),
        sa.Column("public_key_pem", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("jwt_signing_keys")
