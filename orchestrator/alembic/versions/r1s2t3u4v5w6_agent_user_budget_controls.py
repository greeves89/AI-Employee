"""Add budget controls: agent.budget_exceeded_action, user.budget_usd

Revision ID: r1s2t3u4v5w6
Revises: p1b2b2b2b2b2
Create Date: 2026-05-16

- agents.budget_exceeded_action: what to do when the monthly budget is spent
  ("haiku" = downgrade to cheap fallback model, "stop" = block + stop agent)
- users.budget_usd: optional monthly spend cap across all of a user's agents
"""
from alembic import op
import sqlalchemy as sa

revision = "r1s2t3u4v5w6"
down_revision = "p1b2b2b2b2b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column(
            "budget_exceeded_action",
            sa.String(),
            nullable=False,
            server_default="haiku",
        ),
    )
    op.add_column("users", sa.Column("budget_usd", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "budget_usd")
    op.drop_column("agents", "budget_exceeded_action")
