"""agent shared_for_rooms flag — admin-curated agent pool for meeting rooms

Revision ID: f1a2r3o4o5m6
Revises: d1e2v3t4o5k6
Create Date: 2026-07-08

Idempotent: ADD COLUMN IF NOT EXISTS so it is safe on databases that may already
carry the column (server_default keeps existing rows valid).
"""
from alembic import op

revision = "f1a2r3o4o5m6"
down_revision = "d1e2v3t4o5k6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS shared_for_rooms "
        "boolean NOT NULL DEFAULT false"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS shared_for_rooms")
