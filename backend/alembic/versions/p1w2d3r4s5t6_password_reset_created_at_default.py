"""password_reset_tokens.created_at: add missing server default

The model maps created_at via the shared `created_at` alias
(models/base.py), which carries server_default=func.now() — so SQLAlchemy
omits the column from the INSERT and expects the database to supply it.
The creating migration (p1r2e3s4t5k6) declared the column NOT NULL with no
default, so every insert raised NotNullViolationError and admin
password-reset link generation 500'd on any deployment that had never
issued one.

Revision ID: p1w2d3r4s5t6
Revises: a1p2i3k4r5l6
Create Date: 2026-08-25
"""
import sqlalchemy as sa
from alembic import op

revision = "p1w2d3r4s5t6"
down_revision = "a1p2i3k4r5l6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "password_reset_tokens",
        "created_at",
        server_default=sa.text("now()"),
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "password_reset_tokens",
        "created_at",
        server_default=None,
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
    )
