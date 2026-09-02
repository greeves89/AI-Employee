"""agent favorite flag — the one agent pinned to the iOS home dashboard

Revision ID: a1g2e3n4t5f6
Revises: f4a1c9d2e6b7
Create Date: 2026-09-01

Idempotent: ADD COLUMN IF NOT EXISTS so it is safe on databases that may already
carry the column (server_default keeps existing rows valid).
"""
from alembic import op

revision = "a1g2e3n4t5f6"
down_revision = "f4a1c9d2e6b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS favorite "
        "boolean NOT NULL DEFAULT false"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS favorite")
