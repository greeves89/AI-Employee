"""Workflow engine (#392): declarative multi-step agent workflows + their runs.

A ``Workflow`` holds a JSON ``definition`` (a start step + a map of steps). A
``WorkflowRun`` is one execution: the engine (driven by the scheduler tick) walks
the steps, creating agent tasks and advancing on their completion, evaluating
conditions and honouring waits. The visual builder (#394) edits the SAME
definition — one source of truth, no parallel logic.

definition schema (v1):
{
  "start": "s1",
  "steps": {
    "s1": {"type": "agent_task", "title": "...", "prompt": "... {{s0}} ...",
           "agent_id": "abc"|null, "next": "s2"|null},
    "s2": {"type": "condition", "check": {"step": "s1", "op": "contains",
           "value": "OK"}, "true": "s3", "false": null},
    "s3": {"type": "wait", "seconds": 60, "next": "s4"}
  }
}
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Workflow(Base):
    __tablename__ = "workflows"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    user_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    definition: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Optional trigger config, e.g. {"cron": "0 7 * * 1"} — v1 supports manual + cron.
    trigger: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    workflow_id: Mapped[str] = mapped_column(
        String, ForeignKey("workflows.id", ondelete="CASCADE"), index=True
    )
    # running | completed | failed
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running", index=True)
    # {step_id: {"result": "..."}} — outputs of finished steps, for substitution/conditions.
    context: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    current_step: Mapped[str | None] = mapped_column(String, nullable=True)
    # The agent Task this run is currently waiting on (None = not waiting on a task).
    current_task_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # For a wait step: don't advance before this time.
    resume_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    steps_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
