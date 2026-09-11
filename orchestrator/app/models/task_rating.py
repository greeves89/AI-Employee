"""Task rating model for meta-agent improvement tracking."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class TaskRating(Base):
    __tablename__ = "task_ratings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        String, ForeignKey("tasks.id"), nullable=False, index=True
    )
    agent_id: Mapped[str] = mapped_column(
        String, ForeignKey("agents.id"), nullable=False, index=True
    )
    user_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-5
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Ob der Reflection-Richter den Auftrag (nicht nur den Lauf) als erfuellt
    # ansieht — None heisst "konnte nicht urteilen" (z. B. CLI-Fallback), nicht
    # "erfuellt". Siehe _build_reflection_prompt/_parse_reflection_stdout.
    fulfilled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    gap: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Snapshot of task metadata at rating time
    task_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    task_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    task_num_turns: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    task: Mapped["Task"] = relationship("Task")  # noqa: F821
    agent: Mapped["Agent"] = relationship("Agent")  # noqa: F821
