"""refactor_groups_to_roles_remove_group_id

Revision ID: dbb600926993
Revises: cd25b0f773ad
Create Date: 2026-01-30 16:40:41.958558

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'dbb600926993'
down_revision = 'cd25b0f773ad'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Refactor groups to roles and remove group_id from resources.

    Changes:
    1. Rename tables: groups -> roles, group_members -> user_roles, group_permissions -> role_permissions
    2. Drop group_id columns from: agents, chats, functions, mcp_servers, schedules, states, templates, webhooks
    3. Update foreign key references
    """

    # Step 1: Drop group_id columns (and their foreign key constraints)
    # These are automatically dropped when we drop the column in PostgreSQL
    op.drop_column('agents', 'group_id')
    op.drop_column('chats', 'group_id')
    op.drop_column('functions', 'group_id')
    op.drop_column('mcp_servers', 'group_id')
    op.drop_column('schedules', 'group_id')
    op.drop_column('states', 'group_id')
    op.drop_column('templates', 'group_id')
    op.drop_column('webhooks', 'group_id')

    # Step 2: Rename tables
    op.rename_table('groups', 'roles')
    op.rename_table('group_members', 'user_roles')
    op.rename_table('group_permissions', 'role_permissions')

    # Step 3: Rename indexes and constraints
    # groups table indexes
    op.execute('ALTER INDEX ix_groups_name RENAME TO ix_roles_name')
    op.execute('ALTER INDEX ix_groups_email_domain RENAME TO ix_roles_email_domain')
    op.execute('ALTER INDEX ix_groups_external_group_id RENAME TO ix_roles_external_group_id')

    # group_members table indexes and constraints
    op.execute('ALTER INDEX ix_group_members_group_id RENAME TO ix_user_roles_role_id')
    op.execute('ALTER INDEX ix_group_members_user_id RENAME TO ix_user_roles_user_id')

    # group_permissions table indexes and constraints
    op.execute('ALTER INDEX ix_group_permissions_group_id RENAME TO ix_role_permissions_role_id')
    op.execute('ALTER INDEX ix_group_permissions_permission_key RENAME TO ix_role_permissions_permission_key')
    op.execute('ALTER INDEX ix_group_permission_unique RENAME TO ix_role_permission_unique')

    # Step 4: Update column references in renamed tables
    # user_roles.group_id -> user_roles.role_id
    op.alter_column('user_roles', 'group_id', new_column_name='role_id')

    # role_permissions.group_id -> role_permissions.role_id
    op.alter_column('role_permissions', 'group_id', new_column_name='role_id')


def downgrade() -> None:
    """Reverse the migration."""

    # Step 1: Rename columns back
    op.alter_column('user_roles', 'role_id', new_column_name='group_id')
    op.alter_column('role_permissions', 'role_id', new_column_name='group_id')

    # Step 2: Rename indexes back
    op.execute('ALTER INDEX ix_role_permission_unique RENAME TO ix_group_permission_unique')
    op.execute('ALTER INDEX ix_role_permissions_permission_key RENAME TO ix_group_permissions_permission_key')
    op.execute('ALTER INDEX ix_role_permissions_role_id RENAME TO ix_group_permissions_group_id')
    op.execute('ALTER INDEX ix_user_roles_user_id RENAME TO ix_group_members_user_id')
    op.execute('ALTER INDEX ix_user_roles_role_id RENAME TO ix_group_members_group_id')
    op.execute('ALTER INDEX ix_roles_external_group_id RENAME TO ix_groups_external_group_id')
    op.execute('ALTER INDEX ix_roles_email_domain RENAME TO ix_groups_email_domain')
    op.execute('ALTER INDEX ix_roles_name RENAME TO ix_groups_name')

    # Step 3: Rename tables back
    op.rename_table('role_permissions', 'group_permissions')
    op.rename_table('user_roles', 'group_members')
    op.rename_table('roles', 'groups')

    # Step 4: Add back group_id columns
    op.add_column('webhooks', sa.Column('group_id', sa.UUID(), nullable=True))
    op.add_column('templates', sa.Column('group_id', sa.UUID(), nullable=True))
    op.add_column('states', sa.Column('group_id', sa.UUID(), nullable=True))
    op.add_column('schedules', sa.Column('group_id', sa.UUID(), nullable=True))
    op.add_column('mcp_servers', sa.Column('group_id', sa.UUID(), nullable=True))
    op.add_column('functions', sa.Column('group_id', sa.UUID(), nullable=True))
    op.add_column('chats', sa.Column('group_id', sa.UUID(), nullable=True))
    op.add_column('agents', sa.Column('group_id', sa.UUID(), nullable=True))

    # Add foreign keys and indexes back
    op.create_foreign_key(None, 'webhooks', 'groups', ['group_id'], ['id'])
    op.create_foreign_key(None, 'templates', 'groups', ['group_id'], ['id'])
    op.create_foreign_key(None, 'states', 'groups', ['group_id'], ['id'])
    op.create_foreign_key(None, 'schedules', 'groups', ['group_id'], ['id'])
    op.create_foreign_key(None, 'mcp_servers', 'groups', ['group_id'], ['id'])
    op.create_foreign_key(None, 'functions', 'groups', ['group_id'], ['id'])
    op.create_foreign_key(None, 'chats', 'groups', ['group_id'], ['id'])
    op.create_foreign_key(None, 'agents', 'groups', ['group_id'], ['id'])

    op.create_index('ix_webhooks_group_id', 'webhooks', ['group_id'])
    op.create_index('ix_templates_group_id', 'templates', ['group_id'])
    op.create_index('ix_states_group_id', 'states', ['group_id'])
    op.create_index('ix_schedules_group_id', 'schedules', ['group_id'])
    op.create_index('ix_mcp_servers_group_id', 'mcp_servers', ['group_id'])
    op.create_index('ix_functions_group_id', 'functions', ['group_id'])
    op.create_index('ix_chats_group_id', 'chats', ['group_id'])
    op.create_index('ix_agents_group_id', 'agents', ['group_id'])