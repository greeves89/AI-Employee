"""Blinder Prüfer je Projekt: teams.blinder_pruefer

Revision ID: i7j8k9l0m1n2
Revises: f3a4b5c6d7e8
Create Date: 2026-11-07
"""
from alembic import op

revision = "i7j8k9l0m1n2"
down_revision = "f3a4b5c6d7e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE IF EXISTS teams ADD COLUMN IF NOT EXISTS blinder_pruefer BOOLEAN")


def downgrade() -> None:
    op.execute("ALTER TABLE IF EXISTS teams DROP COLUMN IF EXISTS blinder_pruefer")
