"""second_brains: Brain aus einem Bridge-Ordner (bridge_ordner, sync_stand, sync_fehler)

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
Create Date: 2026-10-28
"""
from alembic import op

revision = "d9e0f1a2b3c4"
down_revision = "c8d9e0f1a2b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE second_brains ADD COLUMN IF NOT EXISTS bridge_ordner TEXT")
    op.execute("ALTER TABLE second_brains ADD COLUMN IF NOT EXISTS sync_stand TIMESTAMPTZ")
    op.execute("ALTER TABLE second_brains ADD COLUMN IF NOT EXISTS sync_fehler TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE second_brains DROP COLUMN IF EXISTS sync_fehler")
    op.execute("ALTER TABLE second_brains DROP COLUMN IF EXISTS sync_stand")
    op.execute("ALTER TABLE second_brains DROP COLUMN IF EXISTS bridge_ordner")
