"""manifests.public_info — values a manifest publishes on the unauthenticated /info

Revision ID: p1u2b3l4i5c6
Revises: m1e2t3v4p5d6
Create Date: 2026-09-07
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "p1u2b3l4i5c6"
down_revision = "m1e2t3v4p5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "manifests",
        sa.Column("public_info", sa.JSON, nullable=False, server_default=sa.text("'{}'")),
    )


def downgrade() -> None:
    op.drop_column("manifests", "public_info")
