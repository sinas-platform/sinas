"""sync model drift and add unique file constraints

Revision ID: 148469d401ec
Revises: g1h2i3j4k5l6
Create Date: 2026-03-02 13:01:36.451620

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '148469d401ec'
down_revision = 'g1h2i3j4k5l6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- scheduled_jobs: add missing columns ---
    op.add_column('scheduled_jobs', sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True))
    op.add_column('scheduled_jobs', sa.Column('managed_by', sa.Text(), nullable=True))
    op.add_column('scheduled_jobs', sa.Column('config_name', sa.Text(), nullable=True))
    op.add_column('scheduled_jobs', sa.Column('config_checksum', sa.Text(), nullable=True))

    # --- files: unique filename constraints ---
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

    # --- dependencies: fix renamed index ---
    op.drop_index('ix_installed_packages_package_name', table_name='dependencies')
    op.create_index('ix_dependencies_package_name', 'dependencies', ['package_name'], unique=True)

    # --- packages: consolidate redundant unique constraint + index ---
    op.drop_constraint('packages_name_key', 'packages', type_='unique')
    op.drop_index('ix_packages_name', table_name='packages')
    op.create_index('ix_packages_name', 'packages', ['name'], unique=True)


def downgrade() -> None:
    # --- packages ---
    op.drop_index('ix_packages_name', table_name='packages')
    op.create_index('ix_packages_name', 'packages', ['name'], unique=False)
    op.create_unique_constraint('packages_name_key', 'packages', ['name'])

    # --- dependencies ---
    op.drop_index('ix_dependencies_package_name', table_name='dependencies')
    op.create_index('ix_installed_packages_package_name', 'dependencies', ['package_name'], unique=True)

    # --- files ---
    op.drop_index('uix_files_shared_name', table_name='files', postgresql_where=sa.text("visibility = 'shared'"))
    op.drop_index('uix_files_private_name', table_name='files', postgresql_where=sa.text("visibility = 'private'"))

    # --- scheduled_jobs ---
    op.drop_column('scheduled_jobs', 'config_checksum')
    op.drop_column('scheduled_jobs', 'config_name')
    op.drop_column('scheduled_jobs', 'managed_by')
    op.drop_column('scheduled_jobs', 'updated_at')
