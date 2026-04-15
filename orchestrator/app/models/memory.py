"""Agent long-term memory - persistent knowledge across sessions.

Memory system upgrade (issue #24):
  - Hierarchical rooms (room="project:ai-employee/backend")
  - superseded_by / superseded_at audit trail
  - confidence score (for inferred vs. confirmed facts)
  - access tracking (for hybrid decay scoring)
  - agent_memory_tags: relation table (replaces JSON tags)
  - agent_memory_links: knowledge-graph-light
"""

from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class AgentMemory(Base):
    __tablename__ = "agent_memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(String, index=True)
    category: Mapped[str] = mapped_column(String, index=True)
    key: Mapped[str] = mapped_column(String, index=True)
    content: Mapped[str] = mapped_column(Text)
    importance: Mapped[int] = mapped_column(Integer, default=3)
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

    # --- Issue #24 additions -------------------------------------------------

    # Hierarchical room path, e.g. "project:ai-employee/backend/auth".
    # When NULL, the memory is considered "global" within its category.
    room: Mapped[str | None] = mapped_column(String(500), nullable=True, index=True)

    # Confidence score: 1.0 = directly observed/confirmed, 0.5 = inferred,
    # >1.0 = user-corrected/pinned (should never decay).
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)

    # Access tracking — for the hybrid decay scoring formula.
    appeared_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_appeared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Supersede chain. New version points to the old one via this FK.
    superseded_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("agent_memories.id", ondelete="SET NULL"), nullable=True, index=True
    )
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # "transient" (exponential decay) vs "permanent" (logarithmic decay).
    tag_type: Mapped[str] = mapped_column(String(20), nullable=False, default="permanent")

    # NOTE: `value_hash` is a GENERATED STORED column in Postgres
    # (md5(coalesce(content, ''))). It's used by the UNIQUE index but
    # intentionally NOT mapped to the ORM — SQLAlchemy would otherwise
    # try to include it in INSERT statements and Postgres rejects that.
    # If you need to query by it, use a raw SQL statement.

    tags: Mapped[list["AgentMemoryTag"]] = relationship(
        "AgentMemoryTag", back_populates="memory", cascade="all, delete-orphan"
    )


class AgentMemoryTag(Base):
    __tablename__ = "agent_memory_tags"

    memory_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("agent_memories.id", ondelete="CASCADE"), primary_key=True
    )
    tag: Mapped[str] = mapped_column(String(100), primary_key=True)

    memory: Mapped["AgentMemory"] = relationship("AgentMemory", back_populates="tags")


class AgentMemoryLink(Base):
    __tablename__ = "agent_memory_links"
    __table_args__ = (CheckConstraint("source_id <> target_id", name="ck_agent_memory_links_no_self"),)

    source_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("agent_memories.id", ondelete="CASCADE"), primary_key=True
    )
    target_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("agent_memories.id", ondelete="CASCADE"), primary_key=True
    )
    relation: Mapped[str] = mapped_column(String(50), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
