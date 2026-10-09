"""components: plain HTML, no build step

Components are now an HTML page served as is (no esbuild, no builder
service), so the build output and its status go. css_overrides is folded
into the page (a <style> element); version and is_published were never read.

Revision ID: h1t2m3l4c5p6
Revises: p1k2g3i4n5s6
Create Date: 2026-10-08
"""

import sqlalchemy as sa

from alembic import op

revision = "h1t2m3l4c5p6"
down_revision = "p1k2g3i4n5s6"
branch_labels = None
depends_on = None

_DROPPED = ("compiled_bundle", "source_map", "compile_status", "compile_errors",
            "css_overrides", "version", "is_published")


def upgrade() -> None:
    # Keep any CSS overrides: prepend them to the page as a <style> element.
    op.execute(
        "UPDATE components SET source_code = '<style>' || css_overrides || '</style>' || chr(10) || source_code "
        "WHERE css_overrides IS NOT NULL AND css_overrides <> ''"
    )
    for column in _DROPPED:
        op.drop_column("components", column)


def downgrade() -> None:
    op.add_column("components", sa.Column("is_published", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("components", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("components", sa.Column("css_overrides", sa.Text(), nullable=True))
    op.add_column("components", sa.Column("compile_errors", sa.JSON(), nullable=True))
    op.add_column("components", sa.Column("compile_status", sa.String(50), nullable=False, server_default="pending"))
    op.add_column("components", sa.Column("source_map", sa.Text(), nullable=True))
    op.add_column("components", sa.Column("compiled_bundle", sa.Text(), nullable=True))
