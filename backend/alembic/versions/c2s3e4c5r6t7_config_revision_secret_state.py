"""config revisions: encrypted secret state

Revision ID: c2s3e4c5r6t7
Revises: c1r2e3v4i5s6
Create Date: 2026-10-01

Change history shows secret-bearing fields (connector headers, token
parameters) redacted; their values are kept here, encrypted, so a restore
can still bring the resource back exactly.
"""
import sqlalchemy as sa
from alembic import op

revision = "c2s3e4c5r6t7"
down_revision = "c1r2e3v4i5s6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("config_revisions", sa.Column("secret_state", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("config_revisions", "secret_state")
