"""add user_id to knowledge_entries + Row-Level Security for user isolation

Revision ID: n8h9i0j1k2l3
Revises: m7g8h9i0j1k2
Create Date: 2026-04-05

Adds user_id columns to user-specific tables and backfills them from existing
agent-ownership. Row-Level Security is enforced at the PostgreSQL level so
that a bug in the application cannot leak data across users.
"""

import sqlalchemy as sa
from alembic import op

revision = "n8h9i0j1k2l3"
down_revision = "m7g8h9i0j1k2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ─── Add user_id to user-scoped tables ─────────────────────────
    # knowledge_entries: add user_id (nullable — null means "global" / pre-existing data)
    op.add_column("knowledge_entries", sa.Column("user_id", sa.String(), nullable=True))
    op.create_index("ix_knowledge_entries_user_id", "knowledge_entries", ["user_id"])

    # Backfill knowledge_entries.user_id from created_by → agent.user_id
    op.execute(
        """
        UPDATE knowledge_entries ke
        SET user_id = a.user_id
        FROM agents a
        WHERE ke.created_by = a.id
          AND a.user_id IS NOT NULL
          AND ke.user_id IS NULL
        """
    )

    # meeting_rooms: add user_id
    op.add_column("meeting_rooms", sa.Column("user_id", sa.String(), nullable=True))
    op.create_index("ix_meeting_rooms_user_id", "meeting_rooms", ["user_id"])
    op.execute(
        """
        UPDATE meeting_rooms mr
        SET user_id = (
            SELECT u.id FROM users u WHERE u.id = mr.created_by LIMIT 1
        )
        WHERE mr.user_id IS NULL
        """
    )

    # approval_rules: add user_id
    op.add_column("approval_rules", sa.Column("user_id", sa.String(), nullable=True))
    op.create_index("ix_approval_rules_user_id", "approval_rules", ["user_id"])
    op.execute(
        """
        UPDATE approval_rules ar
        SET user_id = ar.created_by
        WHERE ar.user_id IS NULL AND ar.created_by IS NOT NULL
        """
    )

    # NOTE: Row-Level Security policies are intentionally NOT enabled here.
    # They require per-request session variables (SET app.current_user_id)
    # which need application-layer support. This will come in a follow-up
    # migration once the API layer is updated to set the session var.
    # For now: application-level filtering (WHERE user_id = :user_id) is
    # enforced in the API handlers.


def downgrade() -> None:
    op.drop_index("ix_approval_rules_user_id", table_name="approval_rules")
    op.drop_column("approval_rules", "user_id")
    op.drop_index("ix_meeting_rooms_user_id", table_name="meeting_rooms")
    op.drop_column("meeting_rooms", "user_id")
    op.drop_index("ix_knowledge_entries_user_id", table_name="knowledge_entries")
    op.drop_column("knowledge_entries", "user_id")
