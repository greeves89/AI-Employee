"""Wie oft jemand einen Bereich der Oberfläche geöffnet hat — je Tag gezählt (#1047).

Eine Zeile je Person, Bereich und Tag. Kein Klickprotokoll: weder Uhrzeit noch
Reihenfolge noch Seiteninhalt, nur „Agenten, heute, 4×“. Das reicht für beides,
wofür es da ist — das eigene Menü holt oft Genutztes nach oben, und die
Verwaltung sieht, welche Bereiche auf der Anlage niemand braucht.
"""

from datetime import date

from sqlalchemy import Date, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class BereichAufruf(Base):
    __tablename__ = "bereich_aufrufe"
    __table_args__ = (UniqueConstraint("user_id", "bereich", "tag", name="uq_bereich_aufrufe_tag"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    # Erster Pfadteil („/agents“), bei angelegten Seiten „/p/<kurzname>“.
    bereich: Mapped[str] = mapped_column(String(120))
    tag: Mapped[date] = mapped_column(Date, index=True)
    anzahl: Mapped[int] = mapped_column(Integer, default=0)
