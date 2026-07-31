"""Add mcp_servers.headers_encrypted (custom auth headers)

Revision ID: a1b2c3d4e5f6
Revises: v7h1b2r3d4s5
Create Date: 2026-07-31
"""
from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "v7h1b2r3d4s5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent — the same column is also ensured in the create_all fallback path.
    op.execute("ALTER TABLE mcp_servers ADD COLUMN IF NOT EXISTS headers_encrypted text")


def downgrade() -> None:
    op.drop_column("mcp_servers", "headers_encrypted")
