"""projekt_karten: rueckkanal_id (Rückkanal Issue → Agent, #1123)

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


def downgrade() -> None:
    op.execute("ALTER TABLE IF EXISTS projekt_karten DROP COLUMN IF EXISTS rueckkanal_id")
