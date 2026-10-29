"""Rückkanal Issue → Agent (#1123): projekt_karten.rueckkanal_id, teams.rueckkanal, teams.repo_oeffentlich

Revision ID: d1e2f3a4b5c6
Revises: c0d1e2f3a4b5
Create Date: 2026-10-29
"""
from alembic import op

revision = "d1e2f3a4b5c6"
down_revision = "c0d1e2f3a4b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE IF EXISTS projekt_karten ADD COLUMN IF NOT EXISTS rueckkanal_id BIGINT")
    op.execute("ALTER TABLE IF EXISTS teams ADD COLUMN IF NOT EXISTS rueckkanal BOOLEAN")
    op.execute("ALTER TABLE IF EXISTS teams ADD COLUMN IF NOT EXISTS repo_oeffentlich BOOLEAN")


def downgrade() -> None:
    op.execute("ALTER TABLE IF EXISTS teams DROP COLUMN IF EXISTS repo_oeffentlich")
    op.execute("ALTER TABLE IF EXISTS teams DROP COLUMN IF EXISTS rueckkanal")
    op.execute("ALTER TABLE IF EXISTS projekt_karten DROP COLUMN IF EXISTS rueckkanal_id")
