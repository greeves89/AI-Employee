"""repair: ensure pgvector extension + embedding columns exist

Revision ID: z4a5b6c7d8e9
Revises: y3z4a5b6c7d8
Create Date: 2026-06-24

Some deployments were migrated on a base Postgres image without the pgvector
extension; the embedding migrations were marked applied but the extension and
`embedding vector(1024)` columns never materialised, so semantic search
(brain_search / skill_search / memory) failed with
"column embedding does not exist". This idempotent repair re-creates the
extension, columns and HNSW indexes (no-ops where already present).
"""
from alembic import op

revision = "z4a5b6c7d8e9"
down_revision = "y3z4a5b6c7d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    for tbl in ("knowledge_entries", "agent_memories", "skills"):
        op.execute(f"ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS embedding vector(1024)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_knowledge_entries_embedding "
        "ON knowledge_entries USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_memories_embedding "
        "ON agent_memories USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_skills_embedding "
        "ON skills USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    # Repair migration — intentionally a no-op on downgrade.
    pass
