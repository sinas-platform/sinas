"""add visibility to secrets, update constraints

Revision ID: s1e1c1v1i1s1
Revises: e1x2e3c4t5r6
Create Date: 2026-03-23
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "s1e1c1v1i1s1"
down_revision = "e1x2e3c4t5r6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("secrets", sa.Column("visibility", sa.String(20), nullable=False, server_default="shared"))
    # Drop old unique constraint on name
    op.drop_constraint("secrets_name_key", "secrets", type_="unique")
    # Add partial unique: one shared secret per name
    op.create_index(
        "uq_secret_shared_name",
        "secrets",
        ["name"],
        unique=True,
        postgresql_where=sa.text("visibility = 'shared'"),
    )
    # One private secret per user+name
    op.create_unique_constraint(
        "uq_secret_user_name_visibility",
        "secrets",
        ["user_id", "name", "visibility"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_secret_user_name_visibility", "secrets", type_="unique")
    op.drop_index("uq_secret_shared_name", table_name="secrets")
    op.create_unique_constraint("secrets_name_key", "secrets", ["name"])
    op.drop_column("secrets", "visibility")
