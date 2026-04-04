"""TestRun model — stores results of automated self-test runs."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class TestRun(Base):
    __tablename__ = "test_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Counts
    total: Mapped[int] = mapped_column(Integer, default=0)
    passed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)

    # Detailed results per test
    results: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # [{name, category, status, duration_ms, error, github_issue_url}]

    # Performance metrics snapshot
    performance: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # {avg_response_ms, endpoint_times: {}, agent_metrics: {}}

    # GitHub issues created from failures
    github_issues_created: Mapped[int] = mapped_column(Integer, default=0)

    # Overall status: passed, failed, error
    status: Mapped[str] = mapped_column(String, default="running")

    # Summary text for Telegram digest
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
