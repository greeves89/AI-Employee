"""Make chat message persistence idempotent.

Revision ID: k1l2m3n4o5p6
Revises: i1j2k3l4m5n6
Create Date: 2026-06-02 06:35:00.000000
"""

from alembic import op


revision = "k1l2m3n4o5p6"
down_revision = "i1j2k3l4m5n6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        WITH ranked AS (
            SELECT
                id,
                row_number() OVER (
                    PARTITION BY agent_id, session_id, message_id, role
                    ORDER BY id DESC
                ) AS row_num
            FROM chat_messages
        )
        DELETE FROM chat_messages
        WHERE id IN (
            SELECT id FROM ranked WHERE row_num > 1
        )
        """
    )
    op.create_unique_constraint(
        "uq_chat_messages_agent_session_message_role",
        "chat_messages",
        ["agent_id", "session_id", "message_id", "role"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_chat_messages_agent_session_message_role",
        "chat_messages",
        type_="unique",
    )
