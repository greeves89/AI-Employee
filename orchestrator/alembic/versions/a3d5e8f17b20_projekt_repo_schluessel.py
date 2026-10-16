"""projekt_repo_schluessel: Schlüssel aus der Schlüsselverwaltung für private Repos

Revision ID: a3d5e8f17b20
Revises: f2b7c9d14a63
Create Date: 2026-10-16
"""
from alembic import op

revision = "a3d5e8f17b20"
down_revision = "f2b7c9d14a63"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE teams ADD COLUMN IF NOT EXISTS repo_secret_id INTEGER")


def downgrade() -> None:
    op.execute("ALTER TABLE teams DROP COLUMN IF EXISTS repo_secret_id")
