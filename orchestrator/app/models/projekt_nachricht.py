"""Nachricht im Projektgespräch (core/projekt_gespraech.py).

Menschen und Agenten eines Projekts schreiben hier miteinander. Das System
schreibt mit, was alle wissen sollten: Protokolle von Besprechungen,
Tagesberichte des Leads, abgenommene Ergebnisse.
"""

from sqlalchemy import JSON, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

#: nachricht | frage | entscheidung | antwort | protokoll | bericht | ergebnis
ARTEN = ("nachricht", "frage", "entscheidung", "antwort", "protokoll", "bericht", "ergebnis")


class ProjektNachricht(Base, TimestampMixin):
    __tablename__ = "projekt_nachrichten"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[str] = mapped_column(String(32), index=True)
    # mensch | agent | system
    autor_typ: Mapped[str] = mapped_column(String(10))
    autor_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    autor_name: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text)
    art: Mapped[str] = mapped_column(String(20), default="nachricht")
    # Angesprochene Agenten (IDs); sie bekommen einen Lauf, ihre Antwort kommt hierher zurück.
    an: Mapped[list] = mapped_column(JSON, default=list)
    karte_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Lauf, aus dem diese Nachricht stammt (automatisch gepostete Antwort).
    task_id: Mapped[str | None] = mapped_column(String, nullable=True)
