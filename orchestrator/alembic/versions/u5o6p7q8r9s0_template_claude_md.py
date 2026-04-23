"""Add claude_md column to agent_templates

Revision ID: u5o6p7q8r9s0
Revises: t4n5o6p7q8r9
Create Date: 2026-04-18

Allows each agent template to carry a CLAUDE.md snippet that gets written
into /workspace/CLAUDE.md when an agent is spawned from that template.
This gives template authors a way to inject project-specific instructions
(conventions, tool restrictions, coding style) without modifying the
global system prompt.
"""
from alembic import op
import sqlalchemy as sa

revision = "u5o6p7q8r9s0b"
down_revision = "t4n5o6p7q8r9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Column may already exist from b1b4f9d7981f migration; add only if missing
    from alembic import op as _op
    conn = _op.get_bind()
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
    pass  # column managed by b1b4f9d7981f
