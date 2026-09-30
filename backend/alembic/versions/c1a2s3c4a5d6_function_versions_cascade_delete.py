"""add ondelete=CASCADE to function_versions.function_id

Revision ID: c1a2s3c4a5d6
Revises: 70ea30af567e
Create Date: 2026-09-06 16:35:00.000000

The ORM relationship already declares cascade="all, delete-orphan", but that
only fires on ORM-level session.delete(). Package uninstall (and any other
bulk `DELETE FROM functions ...`) uses a Core `delete()` statement, which
bypasses ORM cascades entirely and hits the FK constraint instead
(IntegrityError: function_versions_function_id_fkey). Making the constraint
itself ON DELETE CASCADE fixes it for every delete path, matching how every
other managed-resource parent/child FK in this schema is already declared
(files -> collections, states -> stores, etc).
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "c1a2s3c4a5d6"
down_revision = "70ea30af567e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "function_versions_function_id_fkey", "function_versions", type_="foreignkey"
    )
    op.create_foreign_key(
        "function_versions_function_id_fkey",
        "function_versions",
        "functions",
        ["function_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "function_versions_function_id_fkey", "function_versions", type_="foreignkey"
    )
    op.create_foreign_key(
        "function_versions_function_id_fkey",
        "function_versions",
        "functions",
        ["function_id"],
        ["id"],
    )
