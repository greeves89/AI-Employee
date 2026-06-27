"""agent.source_agent_id — clone origin for distributed agents

Revision ID: b6c7d8e9f0a1
Revises: a5b6c7d8e9f0
Create Date: 2026-06-24

Tracks which "trained" source agent a distributed clone was copied from. Each
clone is an independent agent; this is just origin metadata. Idempotent — the
orchestrator startup also ensures the column.
"""
from alembic import op
import sqlalchemy as sa


revision = "b6c7d8e9f0a1"
down_revision = "a5b6c7d8e9f0"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "agents", "source_agent_id"):
        op.add_column("agents", sa.Column("source_agent_id", sa.String(), nullable=True))
        op.create_index("ix_agents_source_agent_id", "agents", ["source_agent_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "agents", "source_agent_id"):
        op.drop_index("ix_agents_source_agent_id", table_name="agents")
        op.drop_column("agents", "source_agent_id")
