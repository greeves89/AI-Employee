"""add teams table

Revision ID: c1d2e3f4a5b7
Revises: b2c3d4e5f6a7
Create Date: 2026-06-30

Creates the teams table — a persistent, named group of agents with a
designated lead (Teams feature, Task 1).

Note: the originally-specified revision id c1d2e3f4a5b6 was already taken by
c1d2e3f4a5b6_add_autonomy_level_to_agents.py, so a unique id is used here.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c1d2e3f4a5b7"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "teams",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "member_agent_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("lead_agent_id", sa.String(length=36), nullable=True),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("teams")
