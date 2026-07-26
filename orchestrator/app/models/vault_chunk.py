"""VaultChunk — a chunked, embedded passage of a Second Brain Markdown file.

Bridges the two previously disconnected knowledge layers: the plain-Markdown
Second Brain vaults (grep-only) and the pgvector semantic layer. Each vault file
is split into passage-sized chunks (see ``app.core.chunking``); every chunk
carries a pgvector embedding for semantic search and a Postgres ``tsvector`` for
keyword/BM25 search. Retrieval fuses both signals via Reciprocal Rank Fusion.

The ``embedding`` (vector(1024)) and ``ts`` (generated tsvector) columns are
managed in raw SQL by the migration — matching how ``knowledge_entries`` /
``agent_memories`` handle their pgvector columns — so they are intentionally not
declared as ORM attributes here.
"""
from datetime import datetime, timezone

from sqlalchemy import Integer, String, Text, UniqueConstraint, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class VaultChunk(Base):
    __tablename__ = "vault_chunks"
    __table_args__ = (
        UniqueConstraint("brain_label", "path", "chunk_idx", name="uq_vault_chunk"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Which vault (SecondBrain.label) and which file inside it.
    brain_label: Mapped[str] = mapped_column(String, nullable=False, index=True)
    path: Mapped[str] = mapped_column(String, nullable=False)  # vault-relative file path
    chunk_idx: Mapped[int] = mapped_column(Integer, nullable=False)
    heading: Mapped[str | None] = mapped_column(Text, nullable=True)  # nearest heading path
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Hash of the whole source file — lets the indexer skip unchanged files and
    # replace all chunks of a file atomically when it changes.
    file_hash: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
