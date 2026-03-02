"""add component_shares table

Revision ID: fe02bc34de56
Revises: fe01ab23cd45
Create Date: 2026-02-27

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fe02bc34de56"
down_revision: Union[str, None] = "fe01ab23cd45"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "component_shares",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("component_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("input_data", sa.JSON(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_views", sa.Integer(), nullable=True),
        sa.Column("view_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["component_id"], ["components.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_component_shares_token"), "component_shares", ["token"], unique=True)
    op.create_index(op.f("ix_component_shares_component_id"), "component_shares", ["component_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_component_shares_component_id"), table_name="component_shares")
    op.drop_index(op.f("ix_component_shares_token"), table_name="component_shares")
    op.drop_table("component_shares")
