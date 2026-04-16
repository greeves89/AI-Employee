"""Add skills marketplace: skills table + agent_skill_assignments junction

Revision ID: t4n5o6p7q8r9
Revises: s3m4n5o6p7q8
Create Date: 2026-04-16

Skills are persistent DB entities (not filesystem files). They can be created
by users, agents, or imported from external sources. The junction table
tracks which agents have which skills assigned.
"""
from alembic import op
import sqlalchemy as sa

revision = "t4n5o6p7q8r9"
down_revision = "s3m4n5o6p7q8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "skills",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("name", sa.String(), nullable=False, unique=True),
        sa.Column("description", sa.Text(), server_default=""),
        sa.Column("content", sa.Text(), server_default=""),
        sa.Column("category", sa.String(), server_default="routine"),
        sa.Column("status", sa.String(), server_default="active"),
        sa.Column("created_by", sa.String(), server_default="user"),
        sa.Column("source_url", sa.String(), nullable=True),
        sa.Column("source_repo", sa.String(), nullable=True),
        sa.Column("paths", sa.JSON(), nullable=True),
        sa.Column("roles", sa.JSON(), nullable=True),
        sa.Column("usage_count", sa.Integer(), server_default="0"),
        sa.Column("avg_rating", sa.Float(), nullable=True),
        sa.Column("is_public", sa.Boolean(), server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_skills_name", "skills", ["name"], unique=True)
    op.create_index("ix_skills_category", "skills", ["category"])
    op.create_index("ix_skills_status", "skills", ["status"])

    op.create_table(
        "agent_skill_assignments",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("agent_id", sa.String(), nullable=False),
        sa.Column("skill_id", sa.Integer(), nullable=False),
        sa.Column("assigned_by", sa.String(), server_default="user"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_agent_skill_agent", "agent_skill_assignments", ["agent_id"])
    op.create_index("ix_agent_skill_skill", "agent_skill_assignments", ["skill_id"])
    op.create_unique_constraint("uq_agent_skill", "agent_skill_assignments", ["agent_id", "skill_id"])


def downgrade() -> None:
    op.drop_table("agent_skill_assignments")
    op.drop_table("skills")
