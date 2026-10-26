"""Regel des Bridge-Beobachters: „Wenn ich in X bin, biete Y an“ (#1019).

Die Bridge gleicht aktive Anwendung und Fenstertitel LOKAL gegen die aktiven
Regeln ihres Nutzers ab — ein Treffer kostet kein Token. Erst wenn der Mensch das
Angebot im Overlay annimmt, bekommt der Agent den Auftrag (``auftrag``) samt
aktuellem Bildschirmfoto. Anlegen können der Nutzer selbst und seine Agenten
(„Merk dir: wenn ich in YouTube Studio bin, biete Titel an“).
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class BridgeRegel(Base, TimestampMixin):
    __tablename__ = "bridge_regeln"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    # Welcher Agent den Auftrag bekommt. Leer = der Agent der Bridge-Sitzung.
    agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    name: Mapped[str] = mapped_column(String(120))
    # Bedingungen, beide als „enthält“ ohne Gross-/Kleinschreibung. Mindestens
    # eine ist gesetzt; sind beide gesetzt, müssen beide zutreffen.
    wenn_app: Mapped[str] = mapped_column(String(120), default="")
    wenn_titel: Mapped[str] = mapped_column(String(200), default="")
    # Was im Overlay steht („Soll ich Titel vorschlagen?“) …
    angebot: Mapped[str] = mapped_column(String(300))
    # … und was der Agent nach einem Ja tun soll.
    auftrag: Mapped[str] = mapped_column(Text)
    aktiv: Mapped[bool] = mapped_column(Boolean, default=True)
    # Wer sie angelegt hat: "nutzer" oder "agent:<id>".
    von: Mapped[str] = mapped_column(String(80), default="nutzer")
    treffer: Mapped[int] = mapped_column(Integer, default=0)
    zuletzt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
