"""meeting_rooms: parent_room_id (event-based follow-up)

Revision ID: b2c3d4e5f6a7
Revises: a7b8c9d0e1f2
Create Date: 2026-06-30

The follow-up room waits for the PARENT meeting's assigned action-item TODOs to
be completed, then auto-starts (scheduled_for stays only as the safety cap).
"""
from alembic import op
import sqlalchemy as sa

revision = "b2c3d4e5f6a7"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("meeting_rooms", sa.Column("parent_room_id", sa.String(length=32), nullable=True))
    op.create_index("ix_meeting_rooms_parent_room_id", "meeting_rooms", ["parent_room_id"])


def downgrade() -> None:
    op.drop_index("ix_meeting_rooms_parent_room_id", table_name="meeting_rooms")
    op.drop_column("meeting_rooms", "parent_room_id")
