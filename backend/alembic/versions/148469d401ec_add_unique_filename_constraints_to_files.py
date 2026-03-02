"""add unique filename constraints to files

Revision ID: 148469d401ec
Revises: g1h2i3j4k5l6
Create Date: 2026-03-02 13:01:36.451620

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '148469d401ec'
down_revision = 'g1h2i3j4k5l6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Rename duplicate private files before adding constraint
    op.execute(sa.text("""
        UPDATE files SET name = name || '_' || LEFT(id::text, 8)
        WHERE id IN (
            SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY collection_id, name, user_id ORDER BY created_at
                ) AS rn
                FROM files WHERE visibility = 'private'
            ) dupes WHERE rn > 1
        )
    """))

    # Rename duplicate shared files before adding constraint
    op.execute(sa.text("""
        UPDATE files SET name = name || '_' || LEFT(id::text, 8)
        WHERE id IN (
            SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY collection_id, name ORDER BY created_at
                ) AS rn
                FROM files WHERE visibility = 'shared'
            ) dupes WHERE rn > 1
        )
    """))

    op.create_index(
        'uix_files_private_name', 'files',
        ['collection_id', 'name', 'user_id'],
        unique=True,
        postgresql_where=sa.text("visibility = 'private'"),
    )
    op.create_index(
        'uix_files_shared_name', 'files',
        ['collection_id', 'name'],
        unique=True,
        postgresql_where=sa.text("visibility = 'shared'"),
    )


def downgrade() -> None:
    op.drop_index('uix_files_shared_name', table_name='files', postgresql_where=sa.text("visibility = 'shared'"))
    op.drop_index('uix_files_private_name', table_name='files', postgresql_where=sa.text("visibility = 'private'"))
