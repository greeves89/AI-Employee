"""Persistent checkpoints for long-running jobs — survive container restarts.

Long jobs (multi-step imports, batch processing, crawls) otherwise lose all
progress when the orchestrator container restarts. Each job writes a JobState
row and checkpoints its `step`/`progress_pct`/`last_heartbeat` as it advances.
On startup a resume hook reclassifies rows: jobs with a recent heartbeat are
resumable, stale ones are marked `crashed` and the user is alerted.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class JobState(Base, TimestampMixin):
    __tablename__ = "job_state"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g. "skill_import", "crawl"
    ref_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)  # task_id / agent_id / etc.
    step: Mapped[str] = mapped_column(String, nullable=False, default="")  # human-readable current step
    progress_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="running"
    )  # running | completed | crashed
    last_heartbeat: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    resume_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    job_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (
        Index("ix_job_state_status_heartbeat", "status", "last_heartbeat"),
    )
