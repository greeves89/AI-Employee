"""Adaptive User Profiles — agents learn user preferences automatically.

Each user has one profile that evolves over time. The profile stores
structured dimensions (communication style, technical preferences,
workflow habits, schedule patterns) with per-dimension confidence scores.

Dimensions are updated by the ProfileExtractor service which analyzes
agent memories (category=preference/correction/learning) and task ratings.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class UserProfile(Base, TimestampMixin):
    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True, nullable=False
    )
    profile_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    dimensions: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    last_extracted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Peer Card (Honcho-inspired): compact cross-agent snapshot built from
    # high-confidence memories across ALL of the user's agents. Capped to
    # PEER_CARD_MAX_CHARS (2200) so it fits in every agent's system prompt.
    # JSON: {"facts": [{"text": str, "confidence": float, "agents": [str]}],
    #        "generated_at": iso, "chars": int}
    peer_card: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    peer_card_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class UserProfileEvent(Base):
    """Audit trail for profile changes — tracks what changed, why, and when."""
    __tablename__ = "user_profile_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    dimension: Mapped[str] = mapped_column(String, nullable=False)
    key: Mapped[str] = mapped_column(String, nullable=False)
    old_value: Mapped[str | None] = mapped_column(String, nullable=True)
    new_value: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, default="extraction")
    confidence: Mapped[float] = mapped_column(default=0.5)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
