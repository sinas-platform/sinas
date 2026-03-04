"""fix nullable mismatches on components, database_connections, database_triggers, queries, scheduled_jobs, table_annotations

Revision ID: 666a46f63ac0
Revises: 555f35e529bf
Create Date: 2026-03-03 17:10:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '666a46f63ac0'
down_revision = '555f35e529bf'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # components: JSON list columns — backfill NULLs with []
    for col in ('enabled_agents', 'enabled_functions', 'enabled_queries',
                'enabled_components', 'state_namespaces_readonly', 'state_namespaces_readwrite'):
        op.execute(sa.text(f"UPDATE components SET {col} = '[]' WHERE {col} IS NULL"))
        op.alter_column('components', col,
                        existing_type=postgresql.JSON(astext_type=sa.Text()),
                        nullable=False)

    # database_connections
    op.execute(sa.text("UPDATE database_connections SET config = '{}' WHERE config IS NULL"))
    op.alter_column('database_connections', 'config',
                    existing_type=postgresql.JSON(astext_type=sa.Text()),
                    nullable=False,
                    existing_server_default=sa.text("'{}'::json"))
    for col in ('created_at', 'updated_at'):
        op.alter_column('database_connections', col,
                        existing_type=postgresql.TIMESTAMP(timezone=True),
                        nullable=False,
                        existing_server_default=sa.text('now()'))

    # database_triggers
    op.alter_column('database_triggers', 'is_active',
                    existing_type=sa.BOOLEAN(),
                    nullable=False,
                    existing_server_default=sa.text('true'))
    for col in ('created_at', 'updated_at'):
        op.alter_column('database_triggers', col,
                        existing_type=postgresql.TIMESTAMP(timezone=True),
                        nullable=False,
                        existing_server_default=sa.text('now()'))

    # queries
    for col in ('input_schema', 'output_schema'):
        op.execute(sa.text(f"UPDATE queries SET {col} = '{{}}' WHERE {col} IS NULL"))
        op.alter_column('queries', col,
                        existing_type=postgresql.JSON(astext_type=sa.Text()),
                        nullable=False,
                        existing_server_default=sa.text("'{}'::json"))
    op.alter_column('queries', 'timeout_ms',
                    existing_type=sa.INTEGER(),
                    nullable=False,
                    existing_server_default=sa.text('5000'))
    op.alter_column('queries', 'max_rows',
                    existing_type=sa.INTEGER(),
                    nullable=False,
                    existing_server_default=sa.text('1000'))
    for col in ('created_at', 'updated_at'):
        op.alter_column('queries', col,
                        existing_type=postgresql.TIMESTAMP(timezone=True),
                        nullable=False,
                        existing_server_default=sa.text('now()'))

    # scheduled_jobs
    op.alter_column('scheduled_jobs', 'updated_at',
                    existing_type=postgresql.TIMESTAMP(timezone=True),
                    nullable=False,
                    existing_server_default=sa.text('now()'))

    # table_annotations
    for col in ('created_at', 'updated_at'):
        op.alter_column('table_annotations', col,
                        existing_type=postgresql.TIMESTAMP(timezone=True),
                        nullable=False,
                        existing_server_default=sa.text('now()'))


def downgrade() -> None:
    # table_annotations
    for col in ('updated_at', 'created_at'):
        op.alter_column('table_annotations', col,
                        existing_type=postgresql.TIMESTAMP(timezone=True),
                        nullable=True,
                        existing_server_default=sa.text('now()'))

    # scheduled_jobs
    op.alter_column('scheduled_jobs', 'updated_at',
                    existing_type=postgresql.TIMESTAMP(timezone=True),
                    nullable=True,
                    existing_server_default=sa.text('now()'))

    # queries
    for col in ('updated_at', 'created_at'):
        op.alter_column('queries', col,
                        existing_type=postgresql.TIMESTAMP(timezone=True),
                        nullable=True,
                        existing_server_default=sa.text('now()'))
    op.alter_column('queries', 'max_rows',
                    existing_type=sa.INTEGER(),
                    nullable=True,
                    existing_server_default=sa.text('1000'))
    op.alter_column('queries', 'timeout_ms',
                    existing_type=sa.INTEGER(),
                    nullable=True,
                    existing_server_default=sa.text('5000'))
    for col in ('output_schema', 'input_schema'):
        op.alter_column('queries', col,
                        existing_type=postgresql.JSON(astext_type=sa.Text()),
                        nullable=True,
                        existing_server_default=sa.text("'{}'::json"))

    # database_triggers
    for col in ('updated_at', 'created_at'):
        op.alter_column('database_triggers', col,
                        existing_type=postgresql.TIMESTAMP(timezone=True),
                        nullable=True,
                        existing_server_default=sa.text('now()'))
    op.alter_column('database_triggers', 'is_active',
                    existing_type=sa.BOOLEAN(),
                    nullable=True,
                    existing_server_default=sa.text('true'))

    # database_connections
    for col in ('updated_at', 'created_at'):
        op.alter_column('database_connections', col,
                        existing_type=postgresql.TIMESTAMP(timezone=True),
                        nullable=True,
                        existing_server_default=sa.text('now()'))
    op.alter_column('database_connections', 'config',
                    existing_type=postgresql.JSON(astext_type=sa.Text()),
                    nullable=True,
                    existing_server_default=sa.text("'{}'::json"))

    # components
    for col in ('state_namespaces_readwrite', 'state_namespaces_readonly',
                'enabled_components', 'enabled_queries', 'enabled_functions', 'enabled_agents'):
        op.alter_column('components', col,
                        existing_type=postgresql.JSON(astext_type=sa.Text()),
                        nullable=True)
