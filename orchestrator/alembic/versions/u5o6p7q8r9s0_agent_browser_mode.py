"""Add browser_mode column to agents table for Playwright browser control

Revision ID: ub5oc6pd7qe8
Revises: a2b3c4d5e6f7
Create Date: 2026-04-19

"""
from alembic import op
import sqlalchemy as sa

revision = "ub5oc6pd7qe8"
down_revision = "a2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='agents' AND column_name='browser_mode'"
        )
    )
    if not result.fetchone():
        op.add_column(
            "agents",
            sa.Column("browser_mode", sa.Boolean(), nullable=False, server_default="false"),
        )


def downgrade() -> None:
    op.drop_column("agents", "browser_mode")
