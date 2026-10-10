"""dependencies: config tracking columns

Dependencies move to a resource applier like every other declared kind, so
they get the same ownership columns (who declared them: a config file or a
package). Existing rows stay unmanaged (NULL), as if added by hand.

Revision ID: d3p4e5n6d7s8
Revises: d1f2c3m4p5l6
Create Date: 2026-10-10
"""
import sqlalchemy as sa
from alembic import op

revision = "d3p4e5n6d7s8"
down_revision = "d1f2c3m4p5l6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("dependencies", sa.Column("managed_by", sa.Text(), nullable=True))
    op.add_column("dependencies", sa.Column("config_name", sa.Text(), nullable=True))
    op.add_column("dependencies", sa.Column("config_checksum", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("dependencies", "config_checksum")
    op.drop_column("dependencies", "config_name")
    op.drop_column("dependencies", "managed_by")
