"""projekt_karten: bearbeiter_art, uebernahme, kommentare_gelesen_at (Übernahme mit Ausweis, #1117)

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
Create Date: 2026-10-29
"""
from alembic import op

revision = "b9c0d1e2f3a4"
down_revision = "a8b9c0d1e2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE IF EXISTS projekt_karten ADD COLUMN IF NOT EXISTS bearbeiter_art VARCHAR(20)")
    op.execute("ALTER TABLE IF EXISTS projekt_karten ADD COLUMN IF NOT EXISTS uebernahme JSON")
    op.execute("ALTER TABLE IF EXISTS projekt_karten ADD COLUMN IF NOT EXISTS kommentare_gelesen_at TIMESTAMPTZ")


def downgrade() -> None:
    op.execute("ALTER TABLE IF EXISTS projekt_karten DROP COLUMN IF EXISTS kommentare_gelesen_at")
    op.execute("ALTER TABLE IF EXISTS projekt_karten DROP COLUMN IF EXISTS uebernahme")
    op.execute("ALTER TABLE IF EXISTS projekt_karten DROP COLUMN IF EXISTS bearbeiter_art")
