"""meeting deliverable (taskforce build mode)

Revision ID: c4d5e6f7a8b9
Revises: c3d4e5f6a7b8
Create Date: 2026-07-04

Adds the Taskforce/Deliverable mode to meeting rooms: agents build a real
artifact together in a shared work dir and a coordinator integrates it.
"""
from alembic import op
import sqlalchemy as sa

revision = "c4d5e6f7a8b9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "meeting_rooms",
        sa.Column("deliverable", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "meeting_rooms",
        sa.Column("deliverable_integrated", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("meeting_rooms", "deliverable_integrated")
    op.drop_column("meeting_rooms", "deliverable")
