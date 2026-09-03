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


def upgrade() -> None:
    # Offline-tauglich seit #689: die frueher hier stehende Pruefung per
    # `inspect(bind)` braucht eine echte Verbindung und scheitert bei
    # `alembic upgrade --sql` — was die Vorschau fuer die GANZE
    # nachfolgende Kette blockierte. Die Datenbank kann dieselbe Pruefung
    # selbst (PostgreSQL ist hier gesetzt).
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS source_agent_id varchar")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agents_source_agent_id "
        "ON agents (source_agent_id)"
    )


def downgrade() -> None:
    # Symmetrisch offline-tauglich (#689).
    op.execute("DROP INDEX IF EXISTS ix_agents_source_agent_id")
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS source_agent_id")
