"""meeting_rooms: scheduled_for (auto-start follow-up time)

Revision ID: a7b8c9d0e1f2
Revises: f1a2b3c4d5e6
Create Date: 2026-06-30

When a meeting ends, the agents propose a follow-up date. The follow-up room is
created idle with ``scheduled_for`` set; the scheduler auto-starts it once due.
"""
from alembic import op
import sqlalchemy as sa

revision = "a7b8c9d0e1f2"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "meeting_rooms",
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("meeting_rooms", "scheduled_for")
