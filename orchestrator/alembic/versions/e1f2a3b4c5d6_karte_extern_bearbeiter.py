"""projekt_karten: extern_bearbeiter (wer ein Issue außerhalb des Boards bearbeitet)

Revision ID: e1f2a3b4c5d6
Revises: d9e0f1a2b3c4
Create Date: 2026-10-28
"""
from alembic import op

revision = "e1f2a3b4c5d6"
down_revision = "d9e0f1a2b3c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE IF EXISTS projekt_karten ADD COLUMN IF NOT EXISTS extern_bearbeiter VARCHAR(200)")


def downgrade() -> None:
    op.execute("ALTER TABLE IF EXISTS projekt_karten DROP COLUMN IF EXISTS extern_bearbeiter")
