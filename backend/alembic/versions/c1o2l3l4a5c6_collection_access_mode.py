"""Migrate enabled_collections from string list to dict list with access mode

Revision ID: c1o2l3l4a5c6
Revises: s1y2s3t4o5o6
Create Date: 2026-04-09
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "c1o2l3l4a5c6"
down_revision = "s1y2s3t4o5o6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Convert plain string entries to {"collection": str, "access": "readonly"}
    # Only touch rows where enabled_collections contains at least one non-object element
    op.execute(
        """
        UPDATE agents
        SET enabled_collections = (
            SELECT jsonb_agg(
                CASE
                    WHEN jsonb_typeof(elem) = 'string'
                    THEN jsonb_build_object('collection', elem #>> '{}', 'access', 'readonly')
                    ELSE elem
                END
            )
            FROM jsonb_array_elements(enabled_collections::jsonb) AS elem
        )
        WHERE jsonb_typeof(enabled_collections::jsonb) = 'array'
          AND jsonb_array_length(enabled_collections::jsonb) > 0
        """
    )


def downgrade() -> None:
    # Convert dict entries back to plain strings
    op.execute(
        """
        UPDATE agents
        SET enabled_collections = (
            SELECT jsonb_agg(elem ->> 'collection')
            FROM jsonb_array_elements(enabled_collections::jsonb) AS elem
        )
        WHERE jsonb_typeof(enabled_collections::jsonb) = 'array'
          AND jsonb_array_length(enabled_collections::jsonb) > 0
        """
    )
