"""rename apps to manifests

Revision ID: fa7464554e5f
Revises: 6de188a34285
Create Date: 2026-03-11 00:00:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'fa7464554e5f'
down_revision = '6de188a34285'
branch_labels = None
depends_on = None


def upgrade():
    # Rename the table
    op.rename_table('apps', 'manifests')

    # Rename the unique constraint
    op.drop_constraint('uq_app_namespace_name', 'manifests', type_='unique')
    op.create_unique_constraint('uq_manifest_namespace_name', 'manifests', ['namespace', 'name'])


def downgrade():
    # Rename the unique constraint back
    op.drop_constraint('uq_manifest_namespace_name', 'manifests', type_='unique')
    op.create_unique_constraint('uq_app_namespace_name', 'manifests', ['namespace', 'name'])

    # Rename the table back
    op.rename_table('manifests', 'apps')
