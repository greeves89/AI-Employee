"""Meeting Room model — group chat between 3-4 agents with round-robin messaging."""

from sqlalchemy import String, Text, Integer, Boolean
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
    # Message history stored as JSONB array
    messages: Mapped[list] = mapped_column(JSONB, default=list)
    # Creator
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Active flag
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
