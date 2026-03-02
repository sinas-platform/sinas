"""add encrypted to states

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-02-27

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7a8b9c0d1e2"
down_revision: Union[str, None] = "e6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "states",
        sa.Column("encrypted", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "states",
        sa.Column("encrypted_value", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("states", "encrypted_value")
    op.drop_column("states", "encrypted")
