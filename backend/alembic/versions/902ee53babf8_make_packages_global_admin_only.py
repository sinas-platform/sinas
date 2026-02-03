"""make_packages_global_admin_only

Revision ID: 902ee53babf8
Revises: 2787ef41e735
Create Date: 2026-01-26 09:17:40.475625

"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "902ee53babf8"
down_revision = "2787ef41e735"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Remove per-user package installation, make packages global (admin-only)

    # Drop existing unique constraint (user_id + package_name)
    op.drop_constraint("uix_package_user_name", "installed_packages", type_="unique")

    # Drop index on user_id
    op.drop_index("ix_installed_packages_user_id", table_name="installed_packages")

    # Drop foreign key constraint on user_id
    op.drop_constraint("installed_packages_user_id_fkey", "installed_packages", type_="foreignkey")

    # Drop user_id column (packages are now global)
    op.drop_column("installed_packages", "user_id")

    # Add unique constraint on package_name only
    op.create_unique_constraint("uix_package_name", "installed_packages", ["package_name"])

    # Drop existing installed_by foreign key to recreate it as nullable
    op.drop_constraint(
        "installed_packages_installed_by_fkey", "installed_packages", type_="foreignkey"
    )

    # Recreate installed_by as nullable foreign key (for audit trail)
    op.alter_column("installed_packages", "installed_by", existing_type=sa.UUID(), nullable=True)
    op.create_foreign_key(
        "installed_packages_installed_by_fkey",
        "installed_packages",
        "users",
        ["installed_by"],
        ["id"],
    )


def downgrade() -> None:
    # Restore per-user package installation

    # Drop new unique constraint
    op.drop_constraint("uix_package_name", "installed_packages", type_="unique")

    # Add back user_id column (nullable first to allow migration)
    op.add_column("installed_packages", sa.Column("user_id", sa.UUID(), nullable=True))

    # Create foreign key on user_id
    op.create_foreign_key(
        "installed_packages_user_id_fkey", "installed_packages", "users", ["user_id"], ["id"]
    )

    # Create index on user_id
    op.create_index("ix_installed_packages_user_id", "installed_packages", ["user_id"])

    # Make user_id non-nullable (requires data cleanup first in real scenario)
    # Note: This will fail if there's data without user_id - acceptable for clean downgrade
    op.alter_column("installed_packages", "user_id", existing_type=sa.UUID(), nullable=False)

    # Recreate original unique constraint
    op.create_unique_constraint(
        "uix_package_user_name", "installed_packages", ["user_id", "package_name"]
    )
