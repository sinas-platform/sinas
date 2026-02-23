"""remove MCP servers, agent MCP fields, and per-message function overrides

Revision ID: a6b7c8d9e0f1
Revises: f5a6b7c8d9e0
Create Date: 2026-02-19

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a6b7c8d9e0f1'
down_revision = 'f5a6b7c8d9e0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop MCP columns from agents
    op.drop_column('agents', 'enabled_mcp_tools')
    op.drop_column('agents', 'mcp_tool_parameters')

    # Drop unused per-message override columns from messages
    op.drop_column('messages', 'enabled_mcp_tools')
    op.drop_column('messages', 'enabled_functions')

    # Drop mcp_servers table (drop_table cascades to indexes)
    op.drop_table('mcp_servers')


def downgrade() -> None:
    # Recreate mcp_servers table
    op.create_table(
        'mcp_servers',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('url', sa.String(), nullable=False),
        sa.Column('protocol', sa.String(), server_default='sse', nullable=False),
        sa.Column('api_key', sa.String(), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('last_connected', sa.DateTime(timezone=True), nullable=True),
        sa.Column('connection_status', sa.String(), server_default='disconnected', nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('group_id', sa.Uuid(), nullable=True),
        sa.Column('managed_by', sa.String(), nullable=True),
        sa.Column('config_name', sa.String(), nullable=True),
        sa.Column('config_checksum', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['group_id'], ['roles.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_mcp_servers_name'), 'mcp_servers', ['name'], unique=True)
    op.create_index(op.f('ix_mcp_servers_group_id'), 'mcp_servers', ['group_id'], unique=False)

    # Re-add MCP columns to agents
    op.add_column('agents', sa.Column('enabled_mcp_tools', sa.JSON(), nullable=True))
    op.add_column('agents', sa.Column('mcp_tool_parameters', sa.JSON(), nullable=True))

    # Re-add per-message override columns to messages
    op.add_column('messages', sa.Column('enabled_mcp_tools', sa.JSON(), nullable=True))
    op.add_column('messages', sa.Column('enabled_functions', sa.JSON(), nullable=True))
