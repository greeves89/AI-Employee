"""Add browser_mode column to agents table for Playwright browser control

Revision ID: u5o6p7q8r9s0
Revises: w7q8r9s0t1u2
Create Date: 2026-04-19

browser_mode enables the Playwright MCP server inside the agent container
(COMPUTER_USE_BROWSER=true env var) for headless browser automation.
"""
from alembic import op
import sqlalchemy as sa

revision = "u5o6p7q8r9s0"
down_revision = "w7q8r9s0t1u2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column("browser_mode", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("agents", "browser_mode")
