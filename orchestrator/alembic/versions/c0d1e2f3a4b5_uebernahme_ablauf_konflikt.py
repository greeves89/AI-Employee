"""Übernahmen: Ablauf, Obergrenze, Konflikt (#1120)

projekt_karten: extern_bis, extern_sha, konflikt, konflikt_gemeldet
teams: uebernahme_stunden, max_uebernahmen, abgelaufene_uebernehmen

Revision ID: c0d1e2f3a4b5
Revises: b9c0d1e2f3a4
Create Date: 2026-10-29
"""
from alembic import op

revision = "c0d1e2f3a4b5"
down_revision = "b9c0d1e2f3a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE IF EXISTS projekt_karten ADD COLUMN IF NOT EXISTS extern_bis TIMESTAMPTZ")
    op.execute("ALTER TABLE IF EXISTS projekt_karten ADD COLUMN IF NOT EXISTS extern_sha VARCHAR(64)")
    op.execute("ALTER TABLE IF EXISTS projekt_karten ADD COLUMN IF NOT EXISTS konflikt VARCHAR(300)")
    op.execute("ALTER TABLE IF EXISTS projekt_karten ADD COLUMN IF NOT EXISTS konflikt_gemeldet VARCHAR(300)")
    op.execute("ALTER TABLE IF EXISTS teams ADD COLUMN IF NOT EXISTS uebernahme_stunden INTEGER")
    op.execute("ALTER TABLE IF EXISTS teams ADD COLUMN IF NOT EXISTS max_uebernahmen INTEGER")
    op.execute("ALTER TABLE IF EXISTS teams ADD COLUMN IF NOT EXISTS abgelaufene_uebernehmen BOOLEAN NOT NULL DEFAULT false")


def downgrade() -> None:
    op.execute("ALTER TABLE IF EXISTS teams DROP COLUMN IF EXISTS abgelaufene_uebernehmen")
    op.execute("ALTER TABLE IF EXISTS teams DROP COLUMN IF EXISTS max_uebernahmen")
    op.execute("ALTER TABLE IF EXISTS teams DROP COLUMN IF EXISTS uebernahme_stunden")
    op.execute("ALTER TABLE IF EXISTS projekt_karten DROP COLUMN IF EXISTS konflikt_gemeldet")
    op.execute("ALTER TABLE IF EXISTS projekt_karten DROP COLUMN IF EXISTS konflikt")
    op.execute("ALTER TABLE IF EXISTS projekt_karten DROP COLUMN IF EXISTS extern_sha")
    op.execute("ALTER TABLE IF EXISTS projekt_karten DROP COLUMN IF EXISTS extern_bis")
