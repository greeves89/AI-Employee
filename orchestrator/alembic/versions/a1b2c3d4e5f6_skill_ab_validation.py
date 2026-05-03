"""skill A/B validation: probation fields on skills table

Revision ID: a1b2c3d4e5f6
Revises: v1s2k3r4o5l6
Create Date: 2026-05-03

Note: skill_versions table already created by v1s2k3r4o5l6.
This migration only adds A/B probation tracking columns to skills.
"""
from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "v1s2k3r4o5l6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("skills", sa.Column("improvement_status", sa.String, nullable=True))
    op.add_column("skills", sa.Column("probation_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("skills", sa.Column("pre_improvement_avg_helpfulness", sa.Float, nullable=True))
    op.add_column("skills", sa.Column("pre_improvement_rated_count", sa.Integer, nullable=True))


def downgrade() -> None:
    op.drop_column("skills", "pre_improvement_rated_count")
    op.drop_column("skills", "pre_improvement_avg_helpfulness")
    op.drop_column("skills", "probation_started_at")
    op.drop_column("skills", "improvement_status")
