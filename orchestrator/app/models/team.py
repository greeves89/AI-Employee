"""Team model — a persistent, named group of agents with a designated lead."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
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
    # GitHub-Repo des Projekts (owner/name): Issues und PRs kommen aufs Board
    # (core/projekt_git.py). Setzen darf es nur ein Administrator.
    repo: Mapped[str | None] = mapped_column(String(200), nullable=True, default=None)
    repo_abgleich_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    repo_fehler: Mapped[str | None] = mapped_column(String(300), nullable=True, default=None)
    # Schlüssel aus der Schlüsselverwaltung (agent_secrets.id) für private Repos; leer =
    # GitHub-Verbindung der Anlage (nur bei Projekten von Admins) bzw. anonym.
    repo_secret_id: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    # Slug des Second Brain mit dem Projektwissen (core/projekt_wissen.py).
    wissen_slug: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
