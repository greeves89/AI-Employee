"""merge heads: meeting_room_moderator + template_claude_md chains

Revision ID: b1c2d3e4f5g6
Revises: z0t1u2v3w4x5, u5o6p7q8r9s0b
Create Date: 2026-04-23

"""
from alembic import op
import sqlalchemy as sa

revision = "b1c2d3e4f5g6"
down_revision = ("z0t1u2v3w4x5", "u5o6p7q8r9s0b")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
