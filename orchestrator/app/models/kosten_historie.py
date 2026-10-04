"""Verdichtete Kosten gelöschter Chats und Aufgaben (#896).

Wer Chatnachrichten oder Aufgaben löscht — Aufbewahrungsfrist, „Chat löschen“,
„Agent mit Daten löschen“, Aufgabe löschen, Müllabfuhr —, darf die Kosten nicht
mitlöschen: sonst setzt sich das Monatsbudget zurück und „seit Beginn“ sinkt.
Vor dem Löschen schreibt ``core.kosten.verdichten`` hier je Tag, Agent und Quelle
eine Summe hinein. Kein Inhalt, keine Gesprächs- oder Aufgabenkennung — das
Datenschutzziel der Frist (der Inhalt ist weg) bleibt gewahrt.
"""

from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class KostenHistorie(Base):
    __tablename__ = "kosten_historie"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: Kalendertag (UTC) der ursprünglichen Nachricht bzw. Aufgabe.
    tag: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    #: Agent zum Zeitpunkt des Löschens; None = Aufgabe ohne Agent.
    agent_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    #: Besitzer des Agenten beim Verdichten — damit das Nutzerbudget die Kosten
    #: auch nach dem Löschen des Agenten weiter zählt.
    user_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    #: ``aufgaben`` | ``chat`` (core.kosten.QUELLE_*).
    quelle: Mapped[str] = mapped_column(String(20), nullable=False)
    betrag_usd: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False,
    )
