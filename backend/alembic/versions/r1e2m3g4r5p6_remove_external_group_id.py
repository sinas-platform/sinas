"""Remove external_group_id from roles table (OIDC leftover)

Revision ID: r1e2m3g4r5p6
Revises: c1o2l3l4a5c6
Create Date: 2026-04-09
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "r1e2m3g4r5p6"
down_revision = "c1o2l3l4a5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_roles_external_group_id", table_name="roles", if_exists=True)
    op.drop_column("roles", "external_group_id")


def downgrade() -> None:
    import sqlalchemy as sa
    op.add_column(
        "roles",
        sa.Column("external_group_id", sa.String(255), nullable=True),
    )
    op.create_index("ix_roles_external_group_id", "roles", ["external_group_id"], unique=True)
