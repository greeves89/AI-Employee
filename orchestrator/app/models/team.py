"""Team model — a persistent, named group of agents with a designated lead."""

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Team(Base, TimestampMixin):
    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    # List of agent IDs that belong to the team
    member_agent_ids: Mapped[list] = mapped_column(JSONB, default=list)
    # Designated lead agent (one of the members) — None until assigned
    lead_agent_id: Mapped[str | None] = mapped_column(String(36), nullable=True, default=None)
    # Creator
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Active flag
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Kennfarbe des Projekts (Raum, Karten, Menue) — eine der Farben aus
    # PROJEKT_FARBEN in api/teams.py; leer = Standardfarbe.
    farbe: Mapped[str | None] = mapped_column(String(20), nullable=True, default=None)
    # Arbeitstakt des Projekts in Minuten (leer = aus): der Lead sichtet dann
    # regelmaessig das Board und vergibt die naechsten Karten (core/projekt_takt.py).
    takt_minuten: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    takt_schedule_id: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    # Wem der Ersteller das Projekt freigegeben hat (Nutzer-IDs). Ohne Freigabe
    # sieht ein Projekt NUR, wer es angelegt hat (api/teams._is_team_member).
    geteilt_mit: Mapped[list] = mapped_column(JSONB, default=list)
    # Slug des Second Brain mit dem Projektwissen (core/projekt_wissen.py).
    wissen_slug: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
