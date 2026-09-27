"""schedule chat session

Revision ID: 2b8629fd9a5f
Revises: a1f2c3d4e5f6
Create Date: 2026-09-27

Der Chat, in dem ein Zeitplan angelegt wurde. Jeder Lauf erscheint dort als
Kachel mit Ergebnis — bisher lief ein Zeitplan ohne jeden Chat-Bezug, und sein
Ergebnis kam nur ueber die Glocke an.
"""
from alembic import op
import sqlalchemy as sa

revision = "2b8629fd9a5f"
down_revision = "a1f2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("schedules", sa.Column("chat_session_id", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("schedules", "chat_session_id")
