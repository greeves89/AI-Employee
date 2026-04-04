"""add approval_rules table for user-defined agent approval triggers

Revision ID: k5e6f7g8h9i0
Revises: j4d5e6f7g8h9
Create Date: 2026-04-04
"""

import sqlalchemy as sa
from alembic import op

revision = "k5e6f7g8h9i0"
down_revision = "j4d5e6f7g8h9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "approval_rules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.String(50), server_default="custom", nullable=False),
        sa.Column("threshold", sa.Float(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("agent_id", sa.String(32), nullable=True),
        sa.Column("created_by", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_approval_rules_active", "approval_rules", ["is_active"])


def downgrade() -> None:
    op.drop_index("ix_approval_rules_active", table_name="approval_rules")
    op.drop_table("approval_rules")
