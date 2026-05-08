"""template_skill_ids

Revision ID: d5e6f7g8h9i0
Revises: c4d5e6f7g8h9
Create Date: 2026-05-08

"""
from alembic import op
import sqlalchemy as sa

revision = "d5e6f7g8h9i0"
down_revision = "c4d5e6f7g8h9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_templates",
        sa.Column("skill_ids", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("agent_templates", "skill_ids")
