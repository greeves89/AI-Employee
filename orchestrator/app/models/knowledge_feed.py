"""Knowledge Feeds — unified substrate for #41, #42, #43, #44.

Powers four issue-level features with one schema:
  * #41 MCP Server Auto-Discovery     (feed_type = "mcp_registry")
  * #42 AI News Knowledge Base        (feed_type = "ai_news")
  * #43 Competitor Feature Tracker    (feed_type = "competitor")
  * #44 CLAUDE.md Best-Practice Feed  (feed_type = "best_practices")

Each feed is a "scheduled scraper" with a declarative config blob. A
daily worker hits the source, writes new items as KnowledgeFeedItem
rows, dedupes against (feed_id, external_id), and notifies any listening
agent via Redis pub/sub.
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class KnowledgeFeed(Base):
    __tablename__ = "knowledge_feeds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    feed_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=1440)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    items_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    items: Mapped[list["KnowledgeFeedItem"]] = relationship(
        back_populates="feed", cascade="all, delete-orphan"
    )


class KnowledgeFeedItem(Base):
    __tablename__ = "knowledge_feed_items"
    __table_args__ = (
        UniqueConstraint("feed_id", "external_id", name="uq_feed_item_external_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    feed_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("knowledge_feeds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_id: Mapped[str] = mapped_column(String(500), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    # stored as 'metadata' column in DB but attribute can't be named 'metadata'
    # (reserved by SQLAlchemy's Base). Use meta_json.
    meta_json: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    harvested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    seen: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)

    feed: Mapped["KnowledgeFeed"] = relationship(back_populates="items")
