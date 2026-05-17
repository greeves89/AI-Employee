"""skill usage tracking for chat sessions (task_id nullable + chat_session_id)

Revision ID: c1d2e3f4g5h6
Revises: t3u4v5w6x7y8
Create Date: 2026-05-17
"""
from alembic import op
import sqlalchemy as sa

revision = "c1d2e3f4g5h6"
down_revision = "t3u4v5w6x7y8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A skill can now be used in a chat session, not only inside a task.
    op.alter_column("skill_task_usages", "task_id", existing_type=sa.String(), nullable=True)
    op.add_column(
        "skill_task_usages",
        sa.Column("chat_session_id", sa.String(), nullable=True),
    )
    op.add_column(
        "skill_task_usages",
        sa.Column("source", sa.String(), nullable=False, server_default="task"),
    )
    op.create_index(
        "ix_skill_task_usages_chat_session_id",
        "skill_task_usages",
        ["chat_session_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_skill_task_usages_chat_session_id", table_name="skill_task_usages")
    op.drop_column("skill_task_usages", "source")
    op.drop_column("skill_task_usages", "chat_session_id")
    op.alter_column("skill_task_usages", "task_id", existing_type=sa.String(), nullable=False)
