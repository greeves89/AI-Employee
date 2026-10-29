"""Protokoll des Sekretariats: was es heute getan hat (Seite „Heute“).

Nur, wofür es keine andere Quelle gibt — verschobene Mails (Werkzeug ``ms_move_email``
eines Sekretariats-Agenten, api/mcp_msgraph.py) und was der Nutzer auf der Seite selbst
anstößt (Mail erledigt, Antwort beauftragt, Entwurf bearbeitet). Freigaben, Erinnerungen
und Läufe stehen in ihren eigenen Tabellen und werden für das Protokoll dort gelesen
(core/sekretariat.protokoll).
"""

from sqlalchemy import JSON, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class SekretariatProtokoll(Base, TimestampMixin):
    __tablename__ = "sekretariat_protokoll"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    # Wer gehandelt hat: ein Agent des Sekretariats — oder None, dann der Nutzer selbst.
    agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    # verschoben | erledigt | antwort_beauftragt | entwurf_bearbeitet
    art: Mapped[str] = mapped_column(String(30))
    text: Mapped[str] = mapped_column(Text)
    bezug: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
