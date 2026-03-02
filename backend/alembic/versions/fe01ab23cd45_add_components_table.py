"""add components table

Revision ID: fe01ab23cd45
Revises: f7a8b9c0d1e2
Create Date: 2026-02-27

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fe01ab23cd45"
down_revision: Union[str, None] = "f7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "components",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source_code", sa.Text(), nullable=False),
        sa.Column("compiled_bundle", sa.Text(), nullable=True),
        sa.Column("source_map", sa.Text(), nullable=True),
        sa.Column("compile_status", sa.String(length=50), nullable=False, server_default="pending"),
        sa.Column("compile_errors", sa.JSON(), nullable=True),
        sa.Column("input_schema", sa.JSON(), nullable=True),
        sa.Column("enabled_agents", sa.JSON(), nullable=True),
        sa.Column("enabled_functions", sa.JSON(), nullable=True),
        sa.Column("enabled_queries", sa.JSON(), nullable=True),
        sa.Column("enabled_components", sa.JSON(), nullable=True),
        sa.Column("state_namespaces_readonly", sa.JSON(), nullable=True),
        sa.Column("state_namespaces_readwrite", sa.JSON(), nullable=True),
        sa.Column("css_overrides", sa.Text(), nullable=True),
        sa.Column("visibility", sa.String(length=50), nullable=False, server_default="private"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_published", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("managed_by", sa.Text(), nullable=True),
        sa.Column("config_name", sa.Text(), nullable=True),
        sa.Column("config_checksum", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("namespace", "name", name="uix_component_namespace_name"),
    )
    op.create_index(op.f("ix_components_namespace"), "components", ["namespace"], unique=False)
    op.create_index(op.f("ix_components_name"), "components", ["name"], unique=False)
    op.create_index(op.f("ix_components_user_id"), "components", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_components_user_id"), table_name="components")
    op.drop_index(op.f("ix_components_name"), table_name="components")
    op.drop_index(op.f("ix_components_namespace"), table_name="components")
    op.drop_table("components")
