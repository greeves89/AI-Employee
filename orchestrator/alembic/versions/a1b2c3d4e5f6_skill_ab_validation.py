"""skill A/B validation: SkillVersion table + probation fields

Revision ID: a1b2c3d4e5f6
Revises: z0t1u2v3w4x5
Create Date: 2026-05-03

"""
from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "z0t1u2v3w4x5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "skill_versions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("skill_id", sa.Integer, sa.ForeignKey("skills.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("version_number", sa.Integer, nullable=False),
        sa.Column("content", sa.Text, server_default=""),
        sa.Column("description", sa.Text, server_default=""),
        sa.Column("avg_helpfulness_at_snapshot", sa.Float, nullable=True),
        sa.Column("rated_usages_at_snapshot", sa.Integer, nullable=True),
        sa.Column("created_by", sa.String, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), index=True),
    )

    op.add_column("skills", sa.Column("improvement_status", sa.String, nullable=True))
    op.add_column("skills", sa.Column("probation_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("skills", sa.Column("pre_improvement_avg_helpfulness", sa.Float, nullable=True))
    op.add_column("skills", sa.Column("pre_improvement_rated_count", sa.Integer, nullable=True))


def downgrade() -> None:
    op.drop_column("skills", "pre_improvement_rated_count")
    op.drop_column("skills", "pre_improvement_avg_helpfulness")
    op.drop_column("skills", "probation_started_at")
    op.drop_column("skills", "improvement_status")
    op.drop_table("skill_versions")
