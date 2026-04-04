"""add meeting_rooms table for group agent chats

Revision ID: j4d5e6f7g8h9
Revises: i3c4d5e6f7g8
Create Date: 2026-04-04
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision = "j4d5e6f7g8h9"
down_revision = "i3c4d5e6f7g8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "meeting_rooms",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("topic", sa.Text(), server_default="", nullable=False),
        sa.Column("agent_ids", JSONB(), server_default="[]", nullable=False),
        sa.Column("state", sa.String(20), server_default="idle", nullable=False),
        sa.Column("current_turn", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rounds_completed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_rounds", sa.Integer(), server_default="10", nullable=False),
        sa.Column("messages", JSONB(), server_default="[]", nullable=False),
        sa.Column("created_by", sa.String(100), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("meeting_rooms")
