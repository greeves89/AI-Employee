"""finanzen: Monatsbudget je Projekt, Projektbezug in der Kosten-Historie

Revision ID: e2c4f6a8b0d1
Revises: d8f1a2b3c4e5
Create Date: 2026-10-16
"""
from alembic import op

revision = "e2c4f6a8b0d1"
down_revision = "d8f1a2b3c4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE teams ADD COLUMN IF NOT EXISTS budget_usd DOUBLE PRECISION")
    op.execute("ALTER TABLE kosten_historie ADD COLUMN IF NOT EXISTS team_id VARCHAR(32)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_kosten_historie_team_id ON kosten_historie (team_id)")


def downgrade() -> None:
    op.execute("ALTER TABLE kosten_historie DROP COLUMN IF EXISTS team_id")
    op.execute("ALTER TABLE teams DROP COLUMN IF EXISTS budget_usd")
