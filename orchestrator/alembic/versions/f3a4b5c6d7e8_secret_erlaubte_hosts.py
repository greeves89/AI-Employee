"""Keys mit erlaubten Zielen fuer den Egress-Proxy

Ein Key mit ``erlaubte_hosts`` geht bei eingeschaltetem Egress-Proxy nur noch als
Platzhalter in den Agenten; der Proxy setzt den Wert erst auf dem Weg zu diesen
Zielen ein (core/egress.py). Leer = wie bisher.

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-10-31
"""
from alembic import op

revision = "f3a4b5c6d7e8"
down_revision = "e2f3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent: Der Start legt Tabellen aus den Modellen an, die Spalte kann schon da sein.
    op.execute("ALTER TABLE IF EXISTS agent_secrets ADD COLUMN IF NOT EXISTS erlaubte_hosts JSON")


def downgrade() -> None:
    op.execute("ALTER TABLE IF EXISTS agent_secrets DROP COLUMN IF EXISTS erlaubte_hosts")
