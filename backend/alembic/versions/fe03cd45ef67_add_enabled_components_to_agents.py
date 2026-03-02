"""add enabled_components to agents

Revision ID: fe03cd45ef67
Revises: fe02bc34de56
Create Date: 2026-02-27

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fe03cd45ef67"
down_revision: Union[str, None] = "fe02bc34de56"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column("enabled_components", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agents", "enabled_components")
