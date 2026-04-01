"""fix updated_at server defaults on all tables

Revision ID: f1i2x3u4p5d6
Revises: w1e2b3h4o5k6
Create Date: 2026-03-30
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "f1i2x3u4p5d6"
down_revision = "w1e2b3h4o5k6"
branch_labels = None
depends_on = None

TABLES_WITH_UPDATED_AT = [
    "agents",
    "chats",
    "collections",
    "components",
    "connectors",
    "database_connections",
    "functions",
    "manifests",
    "queries",
    "scheduled_jobs",
    "secrets",
    "skills",
    "stores",
    "templates",
    "webhooks",
]


def upgrade() -> None:
    for table in TABLES_WITH_UPDATED_AT:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN updated_at SET DEFAULT now()")


def downgrade() -> None:
    pass  # No rollback needed — defaults are harmless
