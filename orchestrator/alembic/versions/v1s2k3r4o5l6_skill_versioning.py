"""skill_versioning — SkillVersion table + Skill.current_version + SkillTaskUsage.skill_version

Revision ID: v1s2k3r4o5l6
Revises: b1c2d3e4f5g6
Create Date: 2026-05-03 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "v1s2k3r4o5l6"
down_revision: Union[str, None] = "b1c2d3e4f5g6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "skill_versions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("skill_id", sa.Integer(), sa.ForeignKey("skills.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), server_default=""),
        sa.Column("description", sa.Text(), server_default=""),
        sa.Column("avg_helpfulness_at_snapshot", sa.Float(), nullable=True),
        sa.Column("usage_count_at_snapshot", sa.Integer(), server_default="0"),
        sa.Column("created_by", sa.String(), server_default="system"),
        sa.Column("change_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), index=True),
    )
    op.create_index("ix_skill_versions_skill_version", "skill_versions", ["skill_id", "version_number"], unique=True)

    op.add_column("skills", sa.Column("current_version", sa.Integer(), server_default="1"))

    op.add_column("skill_task_usages", sa.Column("skill_version", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("skill_task_usages", "skill_version")
    op.drop_column("skills", "current_version")
    op.drop_index("ix_skill_versions_skill_version", table_name="skill_versions")
    op.drop_table("skill_versions")
