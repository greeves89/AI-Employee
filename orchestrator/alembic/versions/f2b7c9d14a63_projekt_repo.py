"""projekt_repo: GitHub-Repo am Projekt (Issues und PRs aufs Board)

Revision ID: f2b7c9d14a63
Revises: e6c1a7b93f42
Create Date: 2026-10-16
"""
from alembic import op

revision = "f2b7c9d14a63"
down_revision = "e6c1a7b93f42"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE teams ADD COLUMN IF NOT EXISTS repo VARCHAR(200)")
    op.execute("ALTER TABLE teams ADD COLUMN IF NOT EXISTS repo_abgleich_at TIMESTAMP WITH TIME ZONE")
    op.execute("ALTER TABLE teams ADD COLUMN IF NOT EXISTS repo_fehler VARCHAR(300)")


def downgrade() -> None:
    op.execute("ALTER TABLE teams DROP COLUMN IF EXISTS repo_fehler")
    op.execute("ALTER TABLE teams DROP COLUMN IF EXISTS repo_abgleich_at")
    op.execute("ALTER TABLE teams DROP COLUMN IF EXISTS repo")
