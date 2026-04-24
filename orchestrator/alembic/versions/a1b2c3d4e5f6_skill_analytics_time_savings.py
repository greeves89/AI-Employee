"""skill analytics: manual_duration, avg_agent_duration, skill_task_usages table

Revision ID: a2b3c4d5e6f7
Revises: a2u3v4w5x6y7
Create Date: 2026-04-23

"""
from alembic import op
import sqlalchemy as sa

revision = "a2b3c4d5e6f7"
down_revision = "a2u3v4w5x6y7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("skills", sa.Column("manual_duration_seconds", sa.Integer(), nullable=True))
    op.add_column("skills", sa.Column("avg_agent_duration_ms", sa.Float(), nullable=True))
    op.create_table(
        "skill_task_usages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("skill_id", sa.Integer(), sa.ForeignKey("skills.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("task_id", sa.String(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("agent_id", sa.String(), nullable=False, index=True),
        sa.Column("skill_helpfulness", sa.Integer(), nullable=True),
        sa.Column("agent_self_rating", sa.Integer(), nullable=True),
        sa.Column("user_rating", sa.Integer(), nullable=True),
        sa.Column("task_duration_ms", sa.Integer(), nullable=True),
        sa.Column("task_cost_usd", sa.Float(), nullable=True),
        sa.Column("task_num_turns", sa.Integer(), nullable=True),
        sa.Column("time_saved_seconds", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_skill_task_usages_created_at", "skill_task_usages", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_skill_task_usages_created_at", table_name="skill_task_usages")
    op.drop_table("skill_task_usages")
    op.drop_column("skills", "avg_agent_duration_ms")
    op.drop_column("skills", "manual_duration_seconds")
