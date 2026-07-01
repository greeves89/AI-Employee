"""second_brain mcp exposure (mcp_enabled + mcp_token_encrypted)

Revision ID: a5b6c7d8e9f0
Revises: z4a5b6c7d8e9
Create Date: 2026-06-24

Adds per-brain MCP exposure: a flag and a Fernet-encrypted Bearer token so each
Second Brain vault can be reached by external MCP clients at
POST /api/v1/mcp/brains/<slug>. Idempotent — the orchestrator startup also
ensures these columns, so this migration is the audit-trail copy.
"""
from alembic import op
import sqlalchemy as sa


revision = "a5b6c7d8e9f0"
down_revision = "z4a5b6c7d8e9"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    insp = sa.inspect(bind)
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "second_brains", "mcp_enabled"):
        op.add_column(
            "second_brains",
            sa.Column("mcp_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )
    if not _has_column(bind, "second_brains", "mcp_token_encrypted"):
        op.add_column(
            "second_brains",
            sa.Column("mcp_token_encrypted", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "second_brains", "mcp_token_encrypted"):
        op.drop_column("second_brains", "mcp_token_encrypted")
    if _has_column(bind, "second_brains", "mcp_enabled"):
        op.drop_column("second_brains", "mcp_enabled")
