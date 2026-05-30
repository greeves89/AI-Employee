"""hermes-inspired memory caps and skill curator fields

Revision ID: a1b2c3d4e5f6
Revises: z0t1u2v3w4x5
Create Date: 2026-05-30

Adds:
  - skills.last_used_at  — tracked by skill curator to detect stale skills
  - skills.curator_notes — free-text reason from the curator (e.g. "no usage in 30d")
  - SkillStatus.STALE    — handled at the application layer (no enum change needed)
                           since the column is already a plain String.
"""
from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "z0t1u2v3w4x5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "skills",
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "skills",
        sa.Column("curator_notes", sa.Text(), nullable=True),
    )
    op.create_index("ix_skills_last_used_at", "skills", ["last_used_at"])

    # Memory caps: a NULL evicted_at means "active"; a non-NULL value means
    # the row was dropped by memory_caps.enforce(). Consumers must filter
    # `superseded_by IS NULL AND evicted_at IS NULL` to see active memories.
    op.add_column(
        "agent_memories",
        sa.Column("evicted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_agent_memories_evicted_at", "agent_memories", ["evicted_at"])


def downgrade() -> None:
    op.drop_index("ix_agent_memories_evicted_at", table_name="agent_memories")
    op.drop_column("agent_memories", "evicted_at")
    op.drop_index("ix_skills_last_used_at", table_name="skills")
    op.drop_column("skills", "curator_notes")
    op.drop_column("skills", "last_used_at")
