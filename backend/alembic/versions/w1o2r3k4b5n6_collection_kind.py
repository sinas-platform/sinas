"""collections.kind: discriminate workbenches from user-facing collections

Revision ID: w1o2r3k4b5n6
Revises: m1e2t3v4p5d6
Create Date: 2026-09-01
"""
import sqlalchemy as sa
from alembic import op

revision = "w1o2r3k4b5n6"
down_revision = "m1e2t3v4p5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # server_default keeps every existing row (and every writer that doesn't
    # send the column) a plain collection — the field is purely additive.
    op.add_column(
        "collections",
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="collection"),
    )
    op.create_index("ix_collections_kind", "collections", ["kind"])


def downgrade() -> None:
    op.drop_index("ix_collections_kind", table_name="collections")
    op.drop_column("collections", "kind")
