"""Reflection ("Dreaming") run log — one row per nightly out-of-band reflection run.

The reflection job reads session transcripts (chat messages, task steps, meeting
logs) since the last watermark, extracts facts/learnings/team insights via a
cheap LLM call and writes them through the EXISTING memory/knowledge/skill
paths. This table is the "morning note": what ran, what was written, what is
waiting for approval, and what it cost.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ReflectionRun(Base):
    __tablename__ = "reflection_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # running | completed | failed | budget_exceeded
    status: Mapped[str] = mapped_column(String(30), default="running", index=True)
    # Snapshot of the review mode the run used: auto | hybrid | strict
    mode: Mapped[str] = mapped_column(String(20), default="hybrid")
    # trigger: scheduled | manual
    trigger: Mapped[str] = mapped_column(String(20), default="scheduled")
    # Counters: transcripts_read, facts_new, facts_superseded, pending_approvals,
    # kb_entries, skills_drafted, skipped, agents (list), errors (list)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
