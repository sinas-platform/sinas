"""add tool_call_results partitioned table

Revision ID: t1o2o3l4r5s6
Revises: s1e1c1v1i1s1
Create Date: 2026-03-23
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "t1o2o3l4r5s6"
down_revision = "s1e1c1v1i1s1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create partitioned parent table
    op.execute("""
        CREATE TABLE tool_call_results (
            id              UUID NOT NULL,
            tool_call_id    VARCHAR(255) NOT NULL,
            chat_id         UUID,
            user_id         UUID NOT NULL,
            tool_name       VARCHAR(500) NOT NULL,
            arguments       JSONB,
            result          JSONB,
            result_size     INTEGER,
            status_code     INTEGER,
            duration_ms     INTEGER,
            source          VARCHAR(20) NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at      TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at)
    """)

    # Create indexes on parent (inherited by partitions)
    op.execute("CREATE INDEX idx_tcr_tool_call_id ON tool_call_results (tool_call_id)")
    op.execute("CREATE INDEX idx_tcr_chat_id ON tool_call_results (chat_id)")
    op.execute("CREATE INDEX idx_tcr_user_id ON tool_call_results (user_id)")

    # Create initial partitions (current + next 2 months)
    op.execute("""
        CREATE TABLE tool_call_results_2026_03 PARTITION OF tool_call_results
        FOR VALUES FROM ('2026-03-01') TO ('2026-04-01')
    """)
    op.execute("""
        CREATE TABLE tool_call_results_2026_04 PARTITION OF tool_call_results
        FOR VALUES FROM ('2026-04-01') TO ('2026-05-01')
    """)
    op.execute("""
        CREATE TABLE tool_call_results_2026_05 PARTITION OF tool_call_results
        FOR VALUES FROM ('2026-05-01') TO ('2026-06-01')
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tool_call_results CASCADE")
