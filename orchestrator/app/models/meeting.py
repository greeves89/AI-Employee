"""Saved meeting recordings — transcript + metadata, no audio (see meetings.py)."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


#: Woher eine Besprechung kommt: Mitschnitt im Browser oder Teams-Termin eines Agenten.
QUELLE_AUFNAHME = "aufnahme"
QUELLE_TEAMS = "teams"
#: Stand eines Teams-Termins (services/teams_meetings).
STATUS_GEPLANT, STATUS_LAEUFT, STATUS_BEENDET, STATUS_PROTOKOLL = "geplant", "laeuft", "beendet", "protokoll"


class Meeting(Base):
    __tablename__ = "meetings"
    __table_args__ = (UniqueConstraint("agent_id", "teams_event_id", name="uq_meetings_agent_event"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    title: Mapped[str] = mapped_column(String)
    transcript: Mapped[str] = mapped_column(Text, default="")
    participants: Mapped[list] = mapped_column(JSON, default=list)  # ["Anna", "Ben", ...]
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # ── Teams-Termin eines Agenten (docs/plaene/teams-besprechungen.md) ──
    quelle: Mapped[str] = mapped_column(String(16), default=QUELLE_AUFNAHME, server_default=QUELLE_AUFNAHME)
    agent_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    teams_event_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    beginn: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ende: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    #: Teilnehmer außerhalb der eigenen Organisation dabei.
    gaeste_extern: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    #: Hinweis „Ich schreibe mit“: Ergebnis von ``teams_gateway.senden_nach_regeln``.
    hinweis: Mapped[str | None] = mapped_column(String(20), nullable=True)
    #: Protokoll mit Aufgaben (Markdown), vom Agenten nach dem Termin.
    protokoll: Mapped[str] = mapped_column(Text, default="", server_default="")
