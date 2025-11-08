"""add_ontology_access_to_assistant

Revision ID: 4600f2668ffc
Revises: d997028a998e
Create Date: 2025-11-07 13:15:39.216463

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '4600f2668ffc'
down_revision = 'd997028a998e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add ontology access columns to assistants table
    op.add_column('assistants', sa.Column('ontology_namespaces', sa.JSON(), nullable=True))
    op.add_column('assistants', sa.Column('ontology_concepts', sa.JSON(), nullable=True))


def downgrade() -> None:
    # Remove ontology access columns from assistants table
    op.drop_column('assistants', 'ontology_concepts')
    op.drop_column('assistants', 'ontology_namespaces')