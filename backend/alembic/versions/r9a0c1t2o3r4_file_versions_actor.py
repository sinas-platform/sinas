"""file_versions_actor: record whether a version was written by a user or an agent

Agents write to collections as the user, so uploaded_by always names the
person. actor_type ("user" or "agent") and agent_id say who actually wrote
the version. Existing rows are user writes as far as anyone can tell: the
history does not record which were agent writes, so they backfill to "user".

Revision ID: r9a0c1t2o3r4
Revises: r8e9t0i1d2x3
Create Date: 2026-10-20
"""

import sqlalchemy as sa

from alembic import op
from app.models.base import GUID

revision = "r9a0c1t2o3r4"
down_revision = "r8e9t0i1d2x3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "file_versions",
        sa.Column("actor_type", sa.String(length=16), nullable=False, server_default="user"),
    )
    op.add_column("file_versions", sa.Column("agent_id", GUID(), nullable=True))
    op.create_foreign_key(
        "file_versions_agent_id_fkey",
        "file_versions",
        "agents",
        ["agent_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("file_versions_agent_id_fkey", "file_versions", type_="foreignkey")
    op.drop_column("file_versions", "agent_id")
    op.drop_column("file_versions", "actor_type")
