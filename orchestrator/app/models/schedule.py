import enum
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Schedule(Base, TimestampMixin):
    __tablename__ = "schedules"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cron_expression: Mapped[str | None] = mapped_column(String, nullable=True)  # e.g. "0 9 * * 1" = every Monday 9am
    timezone: Mapped[str] = mapped_column(String, nullable=False, default="UTC")  # IANA name; cron is evaluated in this zone (DST-aware)
    priority: Mapped[int] = mapped_column(Integer, default=1)
    agent_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("agents.id"), nullable=True
    )
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    next_run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    total_runs: Mapped[int] = mapped_column(Integer, default=0)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    # Escalated run LINEAGES in a row (resets on any success, NOT the same as
    # fail_count — a lineage that self-heals through 2 retries before failing
    # is still just one). At the threshold the schedule auto-disables instead
    # of escalating hourly forever — see task_router._escalate_exhausted_task.
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    # Termin eines Projekts (Daily/Weekly/einmalig, core/projekt_termine.py).
    team_id: Mapped[str | None] = mapped_column(String(32), nullable=True, default=None, index=True)
    # Der Chat, in dem der Zeitplan angelegt wurde: jeder Lauf erscheint dort als
    # Kachel. Ohne Herkunft (Formular) schreibt der Zeitplan in ein eigenes
    # Gespraech — siehe task_router.faden_fuer_aufgabe.
    chat_session_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # ── Adaptiver Takt (#941) ────────────────────────────────────────────────
    # Ein Beobachtungs-Job wird bei Leerlauf von selbst langsamer und bei neuer
    # Arbeit sofort wieder schnell. Rechenregel: app.core.adaptiver_takt.
    # Standard AUS — ein bestehender Zeitplan behaelt seinen festen Takt.
    # Nur fuer INTERVALL-Zeitplaene: bei Cron waere die Taktrechnung mehrdeutig.
    adaptive: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # Der Grundtakt, auf den ein Lauf mit Arbeit zurueckstellt. Leer = das
    # Intervall, mit dem der Zeitplan angelegt wurde.
    basis_intervall_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Die Stufenleiter in Sekunden, aufsteigend; leer = STUFEN_STANDARD
    # (30 Min / 2 Std / 4 Std / 8 Std). Stufe 0 ist immer der Grundtakt.
    stufen: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # So viele leere Laeufe hintereinander, BEVOR es langsamer wird.
    leer_schwelle: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default="3"
    )
    # Zustand, vom Werkzeug report_run gefuehrt: leere Laeufe in Folge und die
    # aktuell geltende Stufe. ``interval_seconds`` bleibt das fuehrende Feld —
    # der Scheduler rechnet next_run_at weiterhin daraus.
    leerlaeufe: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    stufe: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    # Der Mensch, der den Zeitplan angelegt hat (users.id). Ein Zeitplan ohne Agent
    # ("Automatisch") gehoert ihm: er sieht, pausiert und loescht ihn, und beim
    # Feuern waehlt der Lastverteiler nur unter SEINEN Agenten (#901). NULL bei
    # Agenten-/Systemzeitplaenen und beim Altbestand.
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
