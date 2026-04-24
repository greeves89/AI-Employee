"""Add claude_md column to agent_templates

Revision ID: u5o6p7q8r9s0b
Revises: ub5oc6pd7qe8
Create Date: 2026-04-18

"""
from alembic import op
import sqlalchemy as sa

revision = "u5o6p7q8r9s0b"
down_revision = "ub5oc6pd7qe8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='agent_templates' AND column_name='claude_md'"
        )
    )
    if not result.fetchone():
        op.add_column(
            "agent_templates",
            sa.Column("claude_md", sa.Text(), nullable=False, server_default=""),
        )


def downgrade() -> None:
    pass
