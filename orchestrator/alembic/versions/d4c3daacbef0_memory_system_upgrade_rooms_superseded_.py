"""memory system upgrade: rooms, superseded_by, confidence, tags, links (#24)

Revision ID: d4c3daacbef0
Revises: 51b28b9b96b3
Create Date: 2026-04-15 19:10:42.415486

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4c3daacbef0'
down_revision: Union[str, None] = '51b28b9b96b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add new columns to agent_memories. All nullable / with defaults so
    # existing rows keep working without a backfill migration.
    op.add_column("agent_memories", sa.Column("room", sa.String(length=500), nullable=True))
    op.add_column("agent_memories", sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"))
    op.add_column("agent_memories", sa.Column("appeared_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("agent_memories", sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("agent_memories", sa.Column("last_appeared_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "agent_memories",
        sa.Column("superseded_by", sa.Integer(), sa.ForeignKey("agent_memories.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column("agent_memories", sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("agent_memories", sa.Column("tag_type", sa.String(length=20), nullable=False, server_default="permanent"))

    # Generated column for dedup: md5 of (key || content), used with UNIQUE
    # constraint so we can dedup at the DB level without racing checks.
    # Postgres GENERATED ALWAYS AS ... STORED is supported since 12.
    op.execute(
        """
        ALTER TABLE agent_memories
        ADD COLUMN value_hash VARCHAR(32) GENERATED ALWAYS AS (md5(coalesce(content, ''))) STORED
        """
    )
    # Unique key on (agent_id, room, key, value_hash) — same content can't
    # be inserted twice in the same (agent, room, key) bucket. Because
    # room is nullable, we use a partial unique index plus a second one
    # for NULL-room rows.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_agent_memory_room_key_value
        ON agent_memories (agent_id, room, key, value_hash)
        WHERE room IS NOT NULL AND superseded_by IS NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_agent_memory_nullroom_key_value
        ON agent_memories (agent_id, key, value_hash)
        WHERE room IS NULL AND superseded_by IS NULL
        """
    )
    # Regular indexes for query speed
    op.create_index("ix_agent_memories_room", "agent_memories", ["room"])
    op.create_index("ix_agent_memories_superseded_by", "agent_memories", ["superseded_by"])

    # NEW TABLE: agent_memory_tags — replaces per-memory JSON tags with a
    # relation table so we can index + search efficiently.
    op.create_table(
        "agent_memory_tags",
        sa.Column("memory_id", sa.Integer(), sa.ForeignKey("agent_memories.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tag", sa.String(length=100), primary_key=True),
    )
    op.create_index("ix_agent_memory_tags_tag", "agent_memory_tags", ["tag"])

    # NEW TABLE: agent_memory_links — knowledge-graph-light. A memory can
    # reference another memory with a typed relation (e.g. "uses", "replaces",
    # "refers_to"). Self-links are disallowed via a CHECK constraint.
    op.create_table(
        "agent_memory_links",
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("agent_memories.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("target_id", sa.Integer(), sa.ForeignKey("agent_memories.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("relation", sa.String(length=50), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("source_id <> target_id", name="ck_agent_memory_links_no_self"),
    )
    op.create_index("ix_agent_memory_links_source", "agent_memory_links", ["source_id"])
    op.create_index("ix_agent_memory_links_target", "agent_memory_links", ["target_id"])

    # Backfill room from category for existing memories (low-risk default)
    op.execute("UPDATE agent_memories SET room = category WHERE room IS NULL")


def downgrade() -> None:
    op.drop_index("ix_agent_memory_links_target", table_name="agent_memory_links")
    op.drop_index("ix_agent_memory_links_source", table_name="agent_memory_links")
    op.drop_table("agent_memory_links")
    op.drop_index("ix_agent_memory_tags_tag", table_name="agent_memory_tags")
    op.drop_table("agent_memory_tags")
    op.drop_index("ix_agent_memories_superseded_by", table_name="agent_memories")
    op.drop_index("ix_agent_memories_room", table_name="agent_memories")
    op.execute("DROP INDEX IF EXISTS uq_agent_memory_nullroom_key_value")
    op.execute("DROP INDEX IF EXISTS uq_agent_memory_room_key_value")
    op.drop_column("agent_memories", "value_hash")
    op.drop_column("agent_memories", "tag_type")
    op.drop_column("agent_memories", "superseded_at")
    op.drop_column("agent_memories", "superseded_by")
    op.drop_column("agent_memories", "last_appeared_at")
    op.drop_column("agent_memories", "last_accessed_at")
    op.drop_column("agent_memories", "appeared_count")
    op.drop_column("agent_memories", "confidence")
    op.drop_column("agent_memories", "room")
