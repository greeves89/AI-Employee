"""Shared Knowledge Base - Obsidian-style documents with backlinks and tags.

Central knowledge store accessible by all agents. Supports:
- Markdown content with [[backlinks]] between entries
- #tags for categorization
- Graph view of connections
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, Integer, String, Text, JSON, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

_GLOBAL = text("user_id IS NULL")
_OWNED = text("user_id IS NOT NULL")


class KnowledgeEntry(Base):
    __tablename__ = "knowledge_entries"

    # Eindeutig ist ein Titel JE MANDANT, nicht ueber alle hinweg. Die frueher
    # globale Bedingung auf `title` widersprach dem Schreibpfad, der laengst nach
    # `(title, user_id)` sucht: die Suche fand nichts, weil der Titel einem anderen
    # Besitzer gehoerte, und das folgende INSERT scheiterte an einer Bedingung, die
    # es nach der Logik des Codes gar nicht geben durfte (Issue #655).
    #
    # Zwei TEILWEISE Indizes statt eines auf `(title, user_id)`: NULLs gelten in
    # einem zusammengesetzten Unique-Index als verschieden, ein einfacher Index
    # wuerde also beliebig viele globale Eintraege gleichen Titels zulassen und
    # damit das bisherige Verhalten aufweichen.
    __table_args__ = (
        Index(
            "uq_knowledge_entries_title_global", "title", unique=True,
            postgresql_where=_GLOBAL, sqlite_where=_GLOBAL,
        ),
        Index(
            "uq_knowledge_entries_title_per_user", "title", "user_id", unique=True,
            postgresql_where=_OWNED, sqlite_where=_OWNED,
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # `index=True` ohne `unique`: die Titelsuche bleibt schnell, auch dort, wo die
    # beiden teilweisen Indizes nicht greifen.
    title: Mapped[str] = mapped_column(String, index=True)
    content: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)  # ["project", "decision", ...]
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)  # agent_id or "user"
    updated_by: Mapped[str | None] = mapped_column(String, nullable=True)
    user_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    access_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
