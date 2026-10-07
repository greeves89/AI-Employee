"""agentenliste: ui_preferences am Konto, last_active_at am Agenten

Revision ID: c7e2a9d4f1b3
Revises: b66153cb8a45
Create Date: 2026-10-07

Listenansicht der Agentenseite: Die Einrichtung der Liste (Ansicht, Spalten,
Sortierung, gespeicherte Filter) liegt am Konto und gilt damit auf jedem Geraet.
``agents.last_active_at`` fuer die Spalte „Zuletzt aktiv“. Beide nullable, ohne
Vorgabe: leer heisst „nichts gespeichert“ bzw. „noch nie aktiv gewesen“.
``IF NOT EXISTS``, damit die Migration auf einer Datenbank, die die Spalten schon
hat (create_all-Pfad), nicht scheitert.
"""
from alembic import op

revision = "c7e2a9d4f1b3"
down_revision = "b66153cb8a45"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS ui_preferences JSON")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS last_active_at TIMESTAMP WITH TIME ZONE")


def downgrade() -> None:
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS last_active_at")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS ui_preferences")
