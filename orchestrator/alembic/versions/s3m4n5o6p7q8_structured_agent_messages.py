"""Add structured messaging fields to agent_messages

Revision ID: s3m4n5o6p7q8
Revises: r2l3m4n5o6p7
Create Date: 2026-04-16

Adds message_id (UUID), message_type, and reply_to fields to enable
structured agent-to-agent communication with conversation threading.
"""
from alembic import op
import sqlalchemy as sa

revision = "s3m4n5o6p7q8"
down_revision = "r2l3m4n5o6p7"  # event_triggers migration
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_messages", sa.Column("message_id", sa.String(), nullable=True))
    op.add_column("agent_messages", sa.Column("message_type", sa.String(), server_default="message", nullable=True))
    op.add_column("agent_messages", sa.Column("reply_to", sa.String(), nullable=True))
    op.create_index("ix_agent_messages_message_id", "agent_messages", ["message_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_agent_messages_message_id", table_name="agent_messages")
    op.drop_column("agent_messages", "reply_to")
    op.drop_column("agent_messages", "message_type")
    op.drop_column("agent_messages", "message_id")
