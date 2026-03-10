"""add stores as first-class state namespace definitions

Revision ID: 7b66e1c1642d
Revises: 666a46f63ac0
Create Date: 2026-03-05 12:00:00.000000

"""
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = '7b66e1c1642d'
down_revision = '666a46f63ac0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create stores table
    op.create_table(
        'stores',
        sa.Column('id', sa.Uuid(), nullable=False, default=uuid.uuid4),
        sa.Column('namespace', sa.String(100), nullable=False, index=True, server_default='default'),
        sa.Column('name', sa.String(100), nullable=False, index=True),
        sa.Column('user_id', sa.Uuid(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('schema', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('strict', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('default_visibility', sa.String(20), nullable=False, server_default='private'),
        sa.Column('encrypted', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('managed_by', sa.Text(), nullable=True),
        sa.Column('config_name', sa.Text(), nullable=True),
        sa.Column('config_checksum', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('namespace', 'name', name='uq_store_namespace_name'),
    )

    # 2. Auto-create stores for existing state namespaces
    # Get the first user_id to use as owner for auto-created stores
    conn = op.get_bind()
    result = conn.execute(text("SELECT DISTINCT namespace FROM states"))
    namespaces = [row[0] for row in result]

    if namespaces:
        # Get a user to own the auto-created stores (prefer admin)
        owner_result = conn.execute(text("SELECT id FROM users LIMIT 1"))
        owner_row = owner_result.first()
        if owner_row:
            owner_id = owner_row[0]
            for ns in namespaces:
                store_id = uuid.uuid4()
                conn.execute(
                    text(
                        "INSERT INTO stores (id, namespace, name, user_id, schema, strict, default_visibility, encrypted, created_at, updated_at) "
                        "VALUES (:id, 'default', :name, :user_id, '{}', false, 'private', false, now(), now())"
                    ),
                    {"id": store_id, "name": ns, "user_id": owner_id}
                )

    # 3. Add store_id column to states (nullable initially)
    op.add_column('states', sa.Column('store_id', sa.Uuid(), nullable=True))

    # 4. Backfill store_id from namespace -> store lookup
    conn.execute(
        text(
            "UPDATE states SET store_id = stores.id "
            "FROM stores "
            "WHERE stores.namespace = 'default' AND stores.name = states.namespace"
        )
    )

    # 5. Make store_id NOT NULL and add FK
    op.alter_column('states', 'store_id', nullable=False)
    op.create_foreign_key(
        'fk_states_store_id',
        'states', 'stores',
        ['store_id'], ['id'],
        ondelete='CASCADE',
    )

    # 6. Drop old indexes and constraints on states
    op.drop_index('uq_state_user_namespace_key', table_name='states')
    op.drop_index('ix_states_namespace_visibility', table_name='states')
    # ix_states_namespace is auto-created from index=True on the column

    # 7. Drop namespace column from states
    op.drop_column('states', 'namespace')

    # 8. Create new indexes
    op.create_index('uq_state_user_store_key', 'states', ['user_id', 'store_id', 'key'], unique=True)
    op.create_index('ix_states_store_visibility', 'states', ['store_id', 'visibility'])
    op.create_index('ix_states_store_id', 'states', ['store_id'])

    # 9. Add enabled_stores to agents
    op.add_column('agents', sa.Column('enabled_stores', sa.JSON(), nullable=False, server_default='[]'))

    # 10. Backfill enabled_stores from state_namespaces on agents
    # Convert readwrite namespaces: ["memory", "prefs"] -> [{"store": "default/memory", "access": "readwrite"}, ...]
    # Convert readonly namespaces: ["config"] -> [{"store": "default/config", "access": "readonly"}, ...]
    conn.execute(
        text("""
            UPDATE agents SET enabled_stores = (
                SELECT COALESCE(
                    jsonb_agg(entry),
                    '[]'::jsonb
                )
                FROM (
                    SELECT jsonb_build_object('store', 'default/' || ns, 'access', 'readwrite') AS entry
                    FROM jsonb_array_elements_text(COALESCE(state_namespaces_readwrite::jsonb, '[]'::jsonb)) AS ns
                    UNION ALL
                    SELECT jsonb_build_object('store', 'default/' || ns, 'access', 'readonly') AS entry
                    FROM jsonb_array_elements_text(COALESCE(state_namespaces_readonly::jsonb, '[]'::jsonb)) AS ns
                ) sub
            )
            WHERE state_namespaces_readonly::text != '[]' OR state_namespaces_readwrite::text != '[]'
        """)
    )

    # 11. Drop old columns from agents
    op.drop_column('agents', 'state_namespaces_readonly')
    op.drop_column('agents', 'state_namespaces_readwrite')

    # 12. Add enabled_stores to components
    op.add_column('components', sa.Column('enabled_stores', sa.JSON(), nullable=False, server_default='[]'))

    # 13. Backfill enabled_stores from state_namespaces on components
    conn.execute(
        text("""
            UPDATE components SET enabled_stores = (
                SELECT COALESCE(
                    jsonb_agg(entry),
                    '[]'::jsonb
                )
                FROM (
                    SELECT jsonb_build_object('store', 'default/' || ns, 'access', 'readwrite') AS entry
                    FROM jsonb_array_elements_text(COALESCE(state_namespaces_readwrite::jsonb, '[]'::jsonb)) AS ns
                    UNION ALL
                    SELECT jsonb_build_object('store', 'default/' || ns, 'access', 'readonly') AS entry
                    FROM jsonb_array_elements_text(COALESCE(state_namespaces_readonly::jsonb, '[]'::jsonb)) AS ns
                ) sub
            )
            WHERE state_namespaces_readonly::text != '[]' OR state_namespaces_readwrite::text != '[]'
        """)
    )

    # 14. Drop old columns from components
    op.drop_column('components', 'state_namespaces_readonly')
    op.drop_column('components', 'state_namespaces_readwrite')

    # 15. Rename state_dependencies to store_dependencies on apps
    op.alter_column('apps', 'state_dependencies', new_column_name='store_dependencies')

    # 16. Backfill store_dependencies format
    # Convert [{"namespace": "x"}] -> [{"store": "default/x"}]
    conn.execute(
        text("""
            UPDATE apps SET store_dependencies = (
                SELECT COALESCE(
                    jsonb_agg(
                        CASE
                            WHEN entry ? 'key'
                            THEN jsonb_build_object('store', 'default/' || (entry->>'namespace'), 'key', entry->>'key')
                            ELSE jsonb_build_object('store', 'default/' || (entry->>'namespace'))
                        END
                    ),
                    '[]'::jsonb
                )
                FROM jsonb_array_elements(COALESCE(store_dependencies::jsonb, '[]'::jsonb)) AS entry
                WHERE entry ? 'namespace'
            )
            WHERE store_dependencies::text LIKE '%namespace%'
        """)
    )


def downgrade() -> None:
    # Re-add old columns to agents
    op.add_column('agents', sa.Column('state_namespaces_readonly', sa.JSON(), nullable=False, server_default='[]'))
    op.add_column('agents', sa.Column('state_namespaces_readwrite', sa.JSON(), nullable=False, server_default='[]'))
    op.drop_column('agents', 'enabled_stores')

    # Re-add old columns to components
    op.add_column('components', sa.Column('state_namespaces_readonly', sa.JSON(), nullable=False, server_default='[]'))
    op.add_column('components', sa.Column('state_namespaces_readwrite', sa.JSON(), nullable=False, server_default='[]'))
    op.drop_column('components', 'enabled_stores')

    # Rename store_dependencies back
    op.alter_column('apps', 'store_dependencies', new_column_name='state_dependencies')

    # Re-add namespace to states
    op.add_column('states', sa.Column('namespace', sa.String(100), nullable=True))

    # Drop new indexes
    op.drop_index('uq_state_user_store_key', table_name='states')
    op.drop_index('ix_states_store_visibility', table_name='states')
    op.drop_index('ix_states_store_id', table_name='states')

    # Drop FK and store_id
    op.drop_constraint('fk_states_store_id', 'states', type_='foreignkey')
    op.drop_column('states', 'store_id')

    # Drop stores table
    op.drop_table('stores')
