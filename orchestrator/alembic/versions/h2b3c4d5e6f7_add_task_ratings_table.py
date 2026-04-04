"""add task_ratings table for meta-agent improvement tracking

Revision ID: h2b3c4d5e6f7
Revises: g1a2b3c4d5e6
Create Date: 2026-04-04
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision = "h2b3c4d5e6f7"
down_revision = "g1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_ratings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.String(), nullable=False),
        sa.Column("agent_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("task_cost_usd", sa.Float(), nullable=True),
        sa.Column("task_duration_ms", sa.Integer(), nullable=True),
        sa.Column("task_num_turns", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_task_ratings_task_id", "task_ratings", ["task_id"])
    op.create_index("ix_task_ratings_agent_id", "task_ratings", ["agent_id"])
    op.create_index("ix_task_ratings_user_id", "task_ratings", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_task_ratings_user_id")
    op.drop_index("ix_task_ratings_agent_id")
    op.drop_index("ix_task_ratings_task_id")
    op.drop_table("task_ratings")
