import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


def is_terminal_task_status(status: "TaskStatus") -> bool:
    """Returns True if the task is in a terminal (non-resumable) state."""
    return status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)


class TaskPriority(int, enum.Enum):
    LOW = 0
    NORMAL = 1
    HIGH = 2
    URGENT = 3


class Task(Base, TimestampMixin):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus), default=TaskStatus.PENDING
    )
    priority: Mapped[int] = mapped_column(Integer, default=TaskPriority.NORMAL)
    agent_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("agents.id"), nullable=True
    )
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    num_turns: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parent_task_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("tasks.id"), nullable=True, index=True
    )
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # --- Task Lifecycle GC fields (inspired by Claude Code ch10) ---
    # notified: parent/orchestrator was informed of completion → safe to schedule eviction
    notified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # retain: UI is actively viewing this task → never auto-evict while True
    retain: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # evict_after: unix timestamp after which the task can be purged from memory/listings
    evict_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    agent: Mapped["Agent | None"] = relationship(back_populates="tasks")  # noqa: F821
    parent_task: Mapped["Task | None"] = relationship(
        "Task", remote_side="Task.id", foreign_keys=[parent_task_id]
    )
    subtasks: Mapped[list["Task"]] = relationship(
        "Task", foreign_keys=[parent_task_id], viewonly=True
    )
