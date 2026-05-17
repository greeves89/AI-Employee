"""Create task_steps table for time-travel replay (issue #54).

Revision ID: f1t2s3t4e5p6
Revises: e1s2k3i4l5l6
Create Date: 2026-05-17
"""

import sqlalchemy as sa
from alembic import op

revision = "f1t2s3t4e5p6"
down_revision = "e1s2k3i4l5l6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_steps",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.String(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("event_data", sa.JSON(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_task_steps_task_id", "task_steps", ["task_id"])
    op.create_index(
        "ix_task_steps_task_sequence", "task_steps", ["task_id", "sequence"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_task_steps_task_sequence", table_name="task_steps")
    op.drop_index("ix_task_steps_task_id", table_name="task_steps")
    op.drop_table("task_steps")
