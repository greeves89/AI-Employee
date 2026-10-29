"""projekt_karten: extern_seit, extern_regung_at, extern_branch (liegen gebliebene fremde Übernahmen)

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-10-29
"""
from alembic import op

revision = "f2a3b4c5d6e7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE IF EXISTS projekt_karten ADD COLUMN IF NOT EXISTS extern_seit TIMESTAMPTZ")
    op.execute("ALTER TABLE IF EXISTS projekt_karten ADD COLUMN IF NOT EXISTS extern_regung_at TIMESTAMPTZ")
    op.execute("ALTER TABLE IF EXISTS projekt_karten ADD COLUMN IF NOT EXISTS extern_branch VARCHAR(200)")


def downgrade() -> None:
    op.execute("ALTER TABLE IF EXISTS projekt_karten DROP COLUMN IF EXISTS extern_branch")
    op.execute("ALTER TABLE IF EXISTS projekt_karten DROP COLUMN IF EXISTS extern_regung_at")
    op.execute("ALTER TABLE IF EXISTS projekt_karten DROP COLUMN IF EXISTS extern_seit")
