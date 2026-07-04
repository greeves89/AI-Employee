"""Meeting Room model — group chat between 3-4 agents with round-robin messaging."""

from datetime import datetime

from sqlalchemy import String, Text, Integer, Boolean, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class MeetingRoom(Base, TimestampMixin):
    __tablename__ = "meeting_rooms"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    topic: Mapped[str] = mapped_column(Text, default="")
    # List of agent IDs participating in the room
    agent_ids: Mapped[list] = mapped_column(JSONB, default=list)
    # Current state: "idle", "running", "paused", "completed"
    state: Mapped[str] = mapped_column(String(20), default="idle")
    # Round-robin tracking: index of agent whose turn it is
    current_turn: Mapped[int] = mapped_column(Integer, default=0)
    # Total rounds completed
    rounds_completed: Mapped[int] = mapped_column(Integer, default=0)
    # Max rounds before auto-stop (0 = unlimited)
    max_rounds: Mapped[int] = mapped_column(Integer, default=10)
    # Optional stage config: [{"name": "Eröffnung", "rounds": 1, "focus": "intro"}, ...]
    # None = legacy flat round-robin mode
    stages_config: Mapped[list | None] = mapped_column(JSONB, nullable=True, default=None)
    # Whether a virtual moderator directs each turn
    use_moderator: Mapped[bool] = mapped_column(Boolean, default=False)
    # Per-meeting moderator LLM: AI-Account id the moderator uses (None = global default)
    moderator_ai_account_id: Mapped[str | None] = mapped_column(String(36), nullable=True, default=None)
    # Follow-up auto-start: this (idle) room starts when all action-item TODOs of the
    # parent meeting are completed (event-based) — scheduled_for is only the safety cap
    # (start no later than this, even if tasks aren't all done).
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    # Parent meeting id — the follow-up waits for THAT meeting's assigned TODOs to finish.
    parent_room_id: Mapped[str | None] = mapped_column(String(32), nullable=True, default=None, index=True)
    # Taskforce/Deliverable mode: the meeting doesn't just produce a todo list — the
    # agents build a REAL artifact together in a shared work dir (/shared/taskforce/{id}/),
    # then a coordinator integrates the parts into one runnable deliverable. Off =
    # classic "discuss + assign todos" meeting (unchanged behaviour).
    deliverable: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Fire-once guard: set True once the integration/assembly task has been dispatched.
    deliverable_integrated: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Message history stored as JSONB array
    messages: Mapped[list] = mapped_column(JSONB, default=list)
    # Creator
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Active flag
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
