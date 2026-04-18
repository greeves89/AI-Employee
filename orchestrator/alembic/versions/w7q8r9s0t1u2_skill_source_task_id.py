"""skill source_task_id column

Revision ID: w7q8r9s0t1u2
Revises: v6p7q8r9s0t1
Create Date: 2026-04-18
"""
from alembic import op
import sqlalchemy as sa

revision = "w7q8r9s0t1u2"
down_revision = "v6p7q8r9s0t1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("skills", sa.Column("source_task_id", sa.String(), nullable=True))
    op.create_index("ix_skills_source_task_id", "skills", ["source_task_id"])


def downgrade() -> None:
    op.drop_index("ix_skills_source_task_id", table_name="skills")
    op.drop_column("skills", "source_task_id")
