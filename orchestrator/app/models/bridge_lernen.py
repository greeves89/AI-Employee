"""„Aus meiner Arbeit lernen“ (#1023): Schalter, Nutzungsprotokoll, Regelvorschläge.

Das Protokoll enthält ausdrücklich nur Anwendung, Fenstertitel und Dauer — nie
ein Bild. Es gehört dem Menschen: er sieht es ein und löscht es. Die Auswertung
macht daraus Vorschläge für Bridge-Regeln; erst sein Ja legt eine Regel an.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class BridgeLernen(Base, TimestampMixin):
    """Der Schalter des Menschen. Fehlt die Zeile, ist Lernen aus."""

    __tablename__ = "bridge_lernen"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    aktiv: Mapped[bool] = mapped_column(Boolean, default=False)
    # Welcher Agent auswertet — der Agent der Bridge-Sitzung, in der zuletzt protokolliert wurde.
    agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    ausgewertet_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)


class BridgeNutzung(Base):
    __tablename__ = "bridge_nutzung"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    app: Mapped[str] = mapped_column(String(120), default="")
    titel: Mapped[str] = mapped_column(String(300), default="")
    dauer_sekunden: Mapped[int] = mapped_column(Integer, default=0)
    ende_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class BridgeVorschlag(Base, TimestampMixin):
    """Ein Regelvorschlag des Agenten. Status: offen → angenommen | abgelehnt."""

    __tablename__ = "bridge_vorschlaege"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    name: Mapped[str] = mapped_column(String(120))
    wenn_app: Mapped[str] = mapped_column(String(120), default="")
    wenn_titel: Mapped[str] = mapped_column(String(200), default="")
    angebot: Mapped[str] = mapped_column(String(300))
    auftrag: Mapped[str] = mapped_column(Text)
    begruendung: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(20), default="offen")
    regel_id: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
