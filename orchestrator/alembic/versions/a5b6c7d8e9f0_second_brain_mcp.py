"""second_brain mcp exposure (mcp_enabled + mcp_token_encrypted)

Revision ID: a5b6c7d8e9f0
Revises: z4a5b6c7d8e9
Create Date: 2026-06-24

Adds per-brain MCP exposure: a flag and a Fernet-encrypted Bearer token so each
Second Brain vault can be reached by external MCP clients at
POST /api/v1/mcp/brains/<slug>. Idempotent — the orchestrator startup also
ensures these columns, so this migration is the audit-trail copy.
"""
from alembic import op
import sqlalchemy as sa


revision = "a5b6c7d8e9f0"
down_revision = "z4a5b6c7d8e9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Offline-tauglich seit #689: die frueher hier stehende Pruefung per
    # `inspect(bind)` braucht eine echte Verbindung und scheitert bei
    # `alembic upgrade --sql` — was die Vorschau fuer die GANZE
    # nachfolgende Kette blockierte. Die Datenbank kann dieselbe Pruefung
    # selbst (PostgreSQL ist hier gesetzt).
    op.execute(
        "ALTER TABLE second_brains ADD COLUMN IF NOT EXISTS mcp_enabled "
        "boolean NOT NULL DEFAULT false"
    )
    op.execute(
        "ALTER TABLE second_brains ADD COLUMN IF NOT EXISTS "
        "mcp_token_encrypted text"
    )


def downgrade() -> None:
    # Symmetrisch offline-tauglich (#689): ein Rueckbau, der erst lesen muss,
    # blockiert die Vorschau genauso wie der Aufbau.
    op.execute("ALTER TABLE second_brains DROP COLUMN IF EXISTS mcp_token_encrypted")
    op.execute("ALTER TABLE second_brains DROP COLUMN IF EXISTS mcp_enabled")
