"""Golden-Tests je Agentenrolle und ihre Läufe (#391).

Ein Prompt-, Modell- oder Skill-Update kann eine Rolle heimlich verschlechtern.
Man merkt es Wochen später an einem falschen Bericht — und weiss dann nicht mehr,
welche Änderung es war.

**Zwei Tabellen, weil es zwei Dinge sind:** die Aufgabensammlung (ändert sich
selten, gehört der Rolle) und der einzelne Lauf (entsteht ständig, gehört einem
Agenten zu einem Zeitpunkt). Sie in eine zu legen hiesse, jede Ausführung würde die
Sammlung überschreiben — und damit wäre der Vergleich mit „vorher" verloren, also
genau das, wofür es das Ganze gibt.
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class EvalSet(Base):
    """Eine versionierte Sammlung von Aufgaben mit erwartetem Ergebnis."""

    __tablename__ = "eval_sets"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    # Für welche Rolle die Sammlung gedacht ist („Buchhaltung", „Support"). Frei
    # gewaehlter Text, wie die Rolle des Agenten selbst — keine Liste.
    role: Mapped[str] = mapped_column(String, default="", index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    # Steigt bei JEDER Änderung an den Aufgaben. Ohne das waere ein Vergleich
    # zwischen zwei Laeufen wertlos: ein besserer Wert koennte auch nur eine
    # leichtere Aufgabe bedeuten.
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    items: Mapped[list] = mapped_column(JSON, default=list)
    user_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class EvalRun(Base):
    """Eine Ausführung einer Sammlung gegen einen bestimmten Agenten."""

    __tablename__ = "eval_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    set_id: Mapped[str] = mapped_column(String, ForeignKey("eval_sets.id"), index=True)
    # Die Fassung, gegen die WIRKLICH gelaufen wurde — nicht die aktuelle. Ändert
    # jemand die Sammlung, bleibt der alte Lauf trotzdem deutbar.
    set_version: Mapped[int] = mapped_column(Integer, default=1)
    agent_id: Mapped[str] = mapped_column(String, index=True)
    # running → completed | failed. Kein Enum: die Zustaende sind hier drei und
    # aendern sich nicht, und ein Enum in SQLite/Postgres zu migrieren kostet mehr
    # als es einbringt.
    status: Mapped[str] = mapped_column(String, default="running", index=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    passed: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    # Wogegen dieser Lauf gemessen wurde. Mitgeschrieben statt nachgeschlagen: die
    # Grundlinie verschiebt sich, und ein alter Lauf soll seine eigene Geschichte
    # erzaehlen.
    baseline_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    regression: Mapped[bool] = mapped_column(Boolean, default=False)
    # Was den Lauf ausgeloest hat: "manual" oder "pre_update".
    trigger: Mapped[str] = mapped_column(String, default="manual")
    results: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
