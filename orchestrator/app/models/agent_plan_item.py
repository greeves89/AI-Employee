"""Der Tagesplan eines Agenten — was er sich fuer heute VORGENOMMEN hat.

Bisher stand der Plan nur in `/workspace/.agent_state.md` im Container: nicht in der
Datenbank, also nirgends anzeigbar und von niemandem korrigierbar. Die Frage „was hat das
Ding heute vor?" war damit unbeantwortbar — man sah nur, was schon erledigt war.

Ein Plan-Eintrag ist bewusst NICHT dasselbe wie ein Todo: das Todo ist die Arbeit, der
Plan-Eintrag ist der Zeitpunkt, zu dem der Agent sie einplant. Ein Eintrag kann auf ein
Todo und spaeter auf die Aufgabe zeigen, die daraus wurde.
"""

from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AgentPlanItem(Base):
    __tablename__ = "agent_plan_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    # Der Tag, fuer den geplant wurde (lokaler Kalendertag des Agenten).
    plan_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)

    title: Mapped[str] = mapped_column(String, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    # Geplanter Beginn (UTC) und geschaetzte Dauer — daraus zeichnet der Kalender den Block.
    planned_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    estimated_minutes: Mapped[int] = mapped_column(Integer, default=30)

    # Woher der Eintrag stammt: "responsibility" (Dauerauftrag), "todo" (offene Arbeit),
    # "self" (der Agent hat es selbst vorgeschlagen), "user" (du hast es reingelegt).
    source: Mapped[str] = mapped_column(String(20), default="self")
    # Vom Verantwortungsbereich bzw. Todo geerbt — steuert die Reihenfolge im Plan.
    priority: Mapped[str] = mapped_column(String(10), default="normal")
    # planned | running | done | dropped — "dropped" heisst: gestrichen (vom Nutzer oder
    # vom Agenten verworfen), bleibt aber sichtbar, damit man den Tag nachvollziehen kann.
    status: Mapped[str] = mapped_column(String(20), default="planned", index=True)

    # Verknuepfungen in die bestehende Welt, beide optional.
    todo_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    task_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # Der Einmal-Zeitplan, der diesen Block zur geplanten Zeit ausloest.
    schedule_id: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
