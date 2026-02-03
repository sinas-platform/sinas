"""update_permissions_to_new_syntax

Revision ID: 4ab62487e5b9
Revises: dbb600926993
Create Date: 2026-01-30 17:58:12.949862

"""
from sqlalchemy import text

from alembic import op

# revision identifiers, used by Alembic.
revision = "4ab62487e5b9"
down_revision = "dbb600926993"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Update permissions from old syntax to new URL-style syntax.

    Changes:
    1. HTTP verbs → Domain verbs (get→read, post→create, put→update)
    2. Multi-word resources use underscores (mcp → mcp_servers, mcp_tools)
    3. Remove :group scope (only :own and :all remain)
    """

    # Delete old format permissions where new format already exists (avoid duplicates)
    # Keep permission with new format, delete one with old format
    op.execute(
        """
        DELETE FROM role_permissions
        WHERE permission_key LIKE '%.get:%'
        AND EXISTS (
            SELECT 1 FROM role_permissions rp2
            WHERE rp2.role_id = role_permissions.role_id
            AND rp2.permission_key = REPLACE(role_permissions.permission_key, '.get:', '.read:')
        )
    """
    )

    op.execute(
        """
        DELETE FROM role_permissions
        WHERE permission_key LIKE '%.post:%'
        AND EXISTS (
            SELECT 1 FROM role_permissions rp2
            WHERE rp2.role_id = role_permissions.role_id
            AND rp2.permission_key = REPLACE(role_permissions.permission_key, '.post:', '.create:')
        )
    """
    )

    op.execute(
        """
        DELETE FROM role_permissions
        WHERE permission_key LIKE '%.put:%'
        AND EXISTS (
            SELECT 1 FROM role_permissions rp2
            WHERE rp2.role_id = role_permissions.role_id
            AND rp2.permission_key = REPLACE(role_permissions.permission_key, '.put:', '.update:')
        )
    """
    )

    # Now update remaining old format permissions to new format
    op.execute(
        """
        UPDATE role_permissions
        SET permission_key = REPLACE(permission_key, '.get:', '.read:')
        WHERE permission_key LIKE '%.get:%'
    """
    )

    op.execute(
        """
        UPDATE role_permissions
        SET permission_key = REPLACE(permission_key, '.post:', '.create:')
        WHERE permission_key LIKE '%.post:%'
    """
    )

    op.execute(
        """
        UPDATE role_permissions
        SET permission_key = REPLACE(permission_key, '.put:', '.update:')
        WHERE permission_key LIKE '%.put:%'
    """
    )

    # Delete old MCP format where new format exists
    op.execute(
        """
        DELETE FROM role_permissions
        WHERE permission_key LIKE 'sinas.mcp.%'
        AND permission_key NOT LIKE '%execute%'
        AND EXISTS (
            SELECT 1 FROM role_permissions rp2
            WHERE rp2.role_id = role_permissions.role_id
            AND rp2.permission_key = REPLACE(role_permissions.permission_key, 'sinas.mcp.', 'sinas.mcp_servers.')
        )
    """
    )

    op.execute(
        """
        DELETE FROM role_permissions
        WHERE permission_key LIKE 'sinas.mcp.execute%'
        AND EXISTS (
            SELECT 1 FROM role_permissions rp2
            WHERE rp2.role_id = role_permissions.role_id
            AND rp2.permission_key = REPLACE(role_permissions.permission_key, 'sinas.mcp.execute', 'sinas.mcp_tools.execute')
        )
    """
    )

    # Update MCP permissions to use underscores
    op.execute(
        """
        UPDATE role_permissions
        SET permission_key = REPLACE(permission_key, 'sinas.mcp.', 'sinas.mcp_servers.')
        WHERE permission_key LIKE 'sinas.mcp.%'
        AND permission_key NOT LIKE '%execute%'
    """
    )

    op.execute(
        """
        UPDATE role_permissions
        SET permission_key = REPLACE(permission_key, 'sinas.mcp.execute', 'sinas.mcp_tools.execute')
        WHERE permission_key LIKE 'sinas.mcp.execute%'
    """
    )

    # Delete :group scope where :own already exists
    # Use chr(58) to avoid SQLAlchemy treating : as bind parameter
    op.execute(
        text(
            """
        DELETE FROM role_permissions
        WHERE permission_key LIKE '%' || chr(58) || 'group'
        AND EXISTS (
            SELECT 1 FROM role_permissions rp2
            WHERE rp2.role_id = role_permissions.role_id
            AND rp2.permission_key = REPLACE(role_permissions.permission_key, chr(58) || 'group', chr(58) || 'own')
        )
    """
        )
    )

    # Remove remaining :group scope - convert to :own
    op.execute(
        text(
            """
        UPDATE role_permissions
        SET permission_key = REPLACE(permission_key, chr(58) || 'group', chr(58) || 'own')
        WHERE permission_key LIKE '%' || chr(58) || 'group'
    """
        )
    )

    # Update any remaining old patterns for consistency
    # Templates, states, and other resources should already be updated by verb changes above


def downgrade() -> None:
    """Downgrade permissions back to old syntax."""

    # Revert domain verbs to HTTP verbs
    op.execute(
        """
        UPDATE role_permissions
        SET permission_key = REPLACE(permission_key, '.read:', '.get:')
        WHERE permission_key LIKE '%.read:%'
    """
    )

    op.execute(
        """
        UPDATE role_permissions
        SET permission_key = REPLACE(permission_key, '.create:', '.post:')
        WHERE permission_key LIKE '%.create:%'
    """
    )

    op.execute(
        """
        UPDATE role_permissions
        SET permission_key = REPLACE(permission_key, '.update:', '.put:')
        WHERE permission_key LIKE '%.update:%'
    """
    )

    # Revert MCP naming
    op.execute(
        """
        UPDATE role_permissions
        SET permission_key = REPLACE(permission_key, 'sinas.mcp_servers.', 'sinas.mcp.')
        WHERE permission_key LIKE 'sinas.mcp_servers.%'
    """
    )

    op.execute(
        """
        UPDATE role_permissions
        SET permission_key = REPLACE(permission_key, 'sinas.mcp_tools.execute', 'sinas.mcp.execute')
        WHERE permission_key LIKE 'sinas.mcp_tools.execute%'
    """
    )

    # Note: Cannot reliably restore :group scope as we don't know which were originally :group
