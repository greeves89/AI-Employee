"""add pgvector embedding column to skills for semantic search

Revision ID: d4e5f6g7h8i9
Revises: c3d4e5f6g7h8
Create Date: 2026-05-03

Adds a native pgvector vector(1024) column to skills for fast cosine similarity
search via HNSW index. This replaces the keyword-only ILIKE search with true
semantic search, matching the approach already used by agent_memories and
knowledge_entries.
"""
from alembic import op
import sqlalchemy as sa

revision = "d4e5f6g7h8i9"
down_revision = "c3d4e5f6g7h8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("ALTER TABLE skills ADD COLUMN IF NOT EXISTS embedding vector(1024)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_skills_embedding "
        "ON skills USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_skills_embedding")
    op.execute("ALTER TABLE skills DROP COLUMN IF EXISTS embedding")
