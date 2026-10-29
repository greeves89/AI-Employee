"""Karte auf dem Board eines Projekts (core/projekt_karten.py).

Eine Karte ist ein Stück Arbeit — selbst angelegt, aus einem Issue oder aus
einem Pull Request. Der Lauf eines Agenten ist der Task dazu (``task_id``):
„vergeben“ legt ihn an, sein Ende schiebt die Karte weiter.
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

SPALTEN = ("eingang", "bereit", "in_arbeit", "pruefung", "erledigt")
STUFEN = ("planung", "bau", "tests", "pr", "ci", "merge")
QUELLEN = ("eigen", "issue", "pr")


class ProjektKarte(Base, TimestampMixin):
    __tablename__ = "projekt_karten"
    __table_args__ = (UniqueConstraint("team_id", "quelle", "extern_nr", name="uq_projekt_karte_extern"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    team_id: Mapped[str] = mapped_column(String(32), index=True)
    quelle: Mapped[str] = mapped_column(String(10), default="eigen")
    # Nummer des Issues/PRs im Repo; bei eigenen Karten leer.
    extern_nr: Mapped[int | None] = mapped_column(Integer, nullable=True)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    titel: Mapped[str] = mapped_column(String(300))
    text: Mapped[str] = mapped_column(Text, default="")
    labels: Mapped[list] = mapped_column(JSON, default=list)
    spalte: Mapped[str] = mapped_column(String(20), default="eingang", index=True)
    # 1 = hoch, 2 = normal, 3 = niedrig
    prioritaet: Mapped[int] = mapped_column(Integer, default=2)
    reihenfolge: Mapped[float] = mapped_column(Float, default=0.0)
    task_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    agent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    stufe: Mapped[str | None] = mapped_column(String(20), nullable=True)
    notiz: Mapped[str | None] = mapped_column(String(500), nullable=True)
    pr_nr: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pr_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # offen | ci_laeuft | ci_rot | ci_gruen | gemergt | geschlossen
    pr_zustand: Mapped[str | None] = mapped_column(String(20), nullable=True)
    erstellt_von: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Nutzer, dem die Karte gehoert (Mensch selbst oder Besitzer des anlegenden
    # Agenten). Geht sie an den Agenten eines ANDEREN Nutzers, ist ihr Text Material,
    # keine Anweisung (gemischte Projekte, core/projekte.karten_auftrag).
    ersteller_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    erledigt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Wer das Issue AUSSERHALB des Boards bearbeitet: Zuständige im Repo („@name“)
    # oder „extern“, wenn nur ein Status-Tag gesetzt ist. Leer = niemand von außen.
    extern_bearbeiter: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Seit wann jemand von außen dran ist, wann er sich zuletzt geregt hat (Issue
    # geändert, Commit auf seinem Branch) und auf welchem Branch er arbeitet. Daran
    # sieht das Board, ob eine fremde Übernahme liegen geblieben ist.
    extern_seit: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    extern_regung_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    extern_branch: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Wer übernommen hat, als Art (core/uebernahme.ARTEN): plattform (ein Agent
    # DIESER Anlage), claude_code, andere_anlage, mensch, extern. Dazu die gültige
    # Übernahme aus den Kommentaren des Issues (``Uebernahme.als_dict``) und wann
    # die Kommentare zuletzt gelesen wurden — neu gelesen wird nur, wenn sich das
    # Issue seitdem geändert hat.
    bearbeiter_art: Mapped[str | None] = mapped_column(String(20), nullable=True)
    uebernahme: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    kommentare_gelesen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Bis wann die Übernahme gilt (core/uebernahme.Uebernahme.ablauf: ``bis`` der
    # Kopfzeile, verlängert durch Lebenszeichen) — danach gilt sie als liegen
    # geblieben. Vom Abgleich berechnet, mit der Dauer des Projekts.
    extern_bis: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Letzter geprüfter Commit auf dem Branch der Übernahme (Autor gegen Inhaber).
    extern_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Konflikt an der Karte (zwei gültige Übernahmen, fremder Commit auf dem Branch)
    # und welcher davon schon gemeldet ist — jeder höchstens einmal.
    konflikt: Mapped[str | None] = mapped_column(String(300), nullable=True)
    konflikt_gemeldet: Mapped[str | None] = mapped_column(String(300), nullable=True)
