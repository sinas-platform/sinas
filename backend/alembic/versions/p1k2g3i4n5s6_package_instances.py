"""packages: declared package name next to the install name (multi-instance installs)

Revision ID: p1k2g3i4n5s6
Revises: m1e2t3v4p5d6
Create Date: 2026-09-07
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "p1k2g3i4n5s6"
down_revision = "m1e2t3v4p5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("packages", sa.Column("package_name", sa.String(255), nullable=True))
    op.create_index("ix_packages_package_name", "packages", ["package_name"])
    # Every existing install is the default instance of its own package.
    op.execute("UPDATE packages SET package_name = name WHERE package_name IS NULL")


def downgrade() -> None:
    op.drop_index("ix_packages_package_name", table_name="packages")
    op.drop_column("packages", "package_name")
