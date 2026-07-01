"""meeting_rooms: per-meeting moderator AI-Account

Revision ID: f1a2b3c4d5e6
Revises: b6c7d8e9f0a1
Create Date: 2026-06-30

Adds an optional per-meeting moderator LLM selection. When NULL the moderator
falls back to the global default (setting ``meeting_moderator_ai_account_id``)
and then to the first available AI-Account.
"""
from alembic import op
import sqlalchemy as sa

revision = "f1a2b3c4d5e6"
down_revision = "b6c7d8e9f0a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "meeting_rooms",
        sa.Column("moderator_ai_account_id", sa.String(length=36), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("meeting_rooms", "moderator_ai_account_id")
