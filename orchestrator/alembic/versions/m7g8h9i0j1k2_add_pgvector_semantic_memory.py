"""add pgvector extension + embedding column for semantic memory search

Revision ID: m7g8h9i0j1k2
Revises: l6f7g8h9i0j1
Create Date: 2026-04-05
"""

import sqlalchemy as sa
from alembic import op

revision = "m7g8h9i0j1k2"
down_revision = "l6f7g8h9i0j1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enable pgvector extension (requires pgvector/pgvector:pg16 image)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Add embedding column to agent_memories (1536 dims = OpenAI text-embedding-3-small)
    op.execute("ALTER TABLE agent_memories ADD COLUMN IF NOT EXISTS embedding vector(1536)")
    # Index for fast similarity search (HNSW is modern + fast)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_memories_embedding "
        "ON agent_memories USING hnsw (embedding vector_cosine_ops)"
    )

    # Add embedding column to knowledge_entries
    op.execute("ALTER TABLE knowledge_entries ADD COLUMN IF NOT EXISTS embedding vector(1536)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_knowledge_entries_embedding "
        "ON knowledge_entries USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_agent_memories_embedding")
    op.execute("DROP INDEX IF EXISTS ix_knowledge_entries_embedding")
    op.execute("ALTER TABLE agent_memories DROP COLUMN IF EXISTS embedding")
    op.execute("ALTER TABLE knowledge_entries DROP COLUMN IF EXISTS embedding")
    # Keep the extension — removing it might break other things
