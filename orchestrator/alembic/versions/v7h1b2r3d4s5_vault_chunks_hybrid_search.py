"""vault_chunks: chunked + embedded Second Brain passages for hybrid search

Adds passage-level retrieval to the Markdown Second Brain vaults, which were
previously pure grep (no semantics). Each vault file is split into 1-3 paragraph
chunks; every chunk gets a pgvector embedding (semantic signal) and a Postgres
tsvector (BM25/keyword signal). Retrieval fuses both via RRF and, when the
embedding service is disabled, falls back to the tsvector/grep path — so the
vault stays searchable even with no embeddings (Pi-friendly).

Also merges the three open heads into one.
"""
from alembic import op

revision = "v7h1b2r3d4s5"
down_revision = ("f1a2r3o4o5m6", "515d03f814a0", "c4d5e6f7a8b9")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS vault_chunks (
            id           BIGSERIAL PRIMARY KEY,
            brain_label  VARCHAR NOT NULL,
            path         VARCHAR NOT NULL,
            chunk_idx    INTEGER NOT NULL,
            heading      TEXT,
            content      TEXT NOT NULL,
            file_hash    VARCHAR NOT NULL,
            embedding    vector(1024),
            ts           tsvector GENERATED ALWAYS AS (to_tsvector('simple', coalesce(content, ''))) STORED,
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_vault_chunk UNIQUE (brain_label, path, chunk_idx)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_vault_chunks_brain_path "
        "ON vault_chunks (brain_label, path)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_vault_chunks_ts "
        "ON vault_chunks USING gin (ts)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_vault_chunks_embedding "
        "ON vault_chunks USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS vault_chunks")
