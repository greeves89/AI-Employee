"""Erinnerung eines Nutzers (Sekretariat, Seite „Heute“): Text und Fälligkeit.

Fällig gewordene Erinnerungen meldet der Zeitplaner einmal als Benachrichtigung
(``core/sekretariat.erinnerungen_melden``). Anlegen können der Nutzer selbst und die
Agenten seines Sekretariats (Werkzeug ``erinnerung_anlegen``).
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Erinnerung(Base, TimestampMixin):
    __tablename__ = "erinnerungen"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    text: Mapped[str] = mapped_column(Text)
    faellig_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # Wer sie angelegt hat: "nutzer" oder "agent:<id>".
    von: Mapped[str] = mapped_column(String(80), default="nutzer")
    gemeldet_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    erledigt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    # Worauf sie sich bezieht: {"art": "mail", "mail_id", "link", "betreff"} oder
    # {"art": "freigabe", "approval_id", "betreff"} (Nachfassen). Setzt nur der Server.
    bezug: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
