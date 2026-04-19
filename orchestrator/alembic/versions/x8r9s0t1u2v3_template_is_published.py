"""add is_published + published_at to agent_templates

Revision ID: x8r9s0t1u2v3
Revises: w7q8r9s0t1u2
Create Date: 2026-04-19
"""
from alembic import op
import sqlalchemy as sa

revision = "x8r9s0t1u2v3"
down_revision = "w7q8r9s0t1u2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_templates",
        sa.Column("is_published", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column(
        "agent_templates",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_agent_templates_is_published", "agent_templates", ["is_published"])


def downgrade() -> None:
    op.drop_index("ix_agent_templates_is_published", table_name="agent_templates")
    op.drop_column("agent_templates", "published_at")
    op.drop_column("agent_templates", "is_published")
