"""Entscheidet nach einem fehlgeschlagenen ``alembic upgrade head``, ob gestempelt werden darf (#796).

Der Rueckfall beim Start — ``create_all`` aus den Modellen plus ``alembic stamp
head`` — war fuer eine FRISCHE Datenbank gedacht. Auf einer bereits
versorgten Anlage tat er etwas anderes: ``create_all`` legt nur fehlende
Tabellen an, ALTERt aber nie eine bestehende; ``stamp head`` erklaert danach
alle offenen Migrationen fuer erledigt, obwohl keine gelaufen ist. Beim
naechsten Start gibt es nichts mehr zu migrieren, die Spalten fehlen weiter,
und der Fehler ist DAUERHAFT: wer die Ursache des Fehlschlags behebt, gewinnt
nichts mehr, weil ``upgrade head`` jetzt ein No-op ist. Beim Melder war nach
einem Update von 1.315.3 auf 1.322.47 ``alembic_version`` von ``a1g2e3n4t5f6``
auf head gesprungen, waehrend ``agents.access_policy`` fehlte — Anwendung
startet nicht mehr.

Sichtbar wurde das erst, seit die Merge-Migration ``3c58f5d6c519`` die
mehreren heads (#721) zusammengefuehrt hat: solange es mehrere heads gab,
scheiterte auch ``stamp head``, und ``alembic_version`` blieb wahr.

Drei Regeln:

* **Als frisch behandelt (``create_all`` + Stempel) wird nur eine frische
  Datenbank** — ``alembic_version`` fehlt oder ist leer. Auf einer versorgten
  Anlage bleibt die Revision stehen, und auch ``create_all`` unterbleibt: eine
  Tabelle, die es aus dem Modell anlegt, laesst die offene Migration beim
  naechsten Start an ``DuplicateTable`` scheitern — wieder dauerhaft, nur
  anders. Der naechste Start versucht ``upgrade head`` unveraendert erneut. Ist
  der Zustand nicht feststellbar (Verbindung weg), gilt „nicht frisch": ein
  ausgelassener Stempel kostet einen zweiten Start, ein falscher Stempel die
  Anlage.
* **Nach einem Timeout wird nie gestempelt** — auch nicht auf einer frischen
  Datenbank: der Prozess koennte noch laufen. Der Timeout ist ueber
  ``ALEMBIC_UPGRADE_TIMEOUT_SECONDS`` einstellbar und deutlich laenger als die
  bisherigen 30 s, weil Datenmigrationen (Backfill auf grossen Tabellen)
  legitim Minuten brauchen.
* **Protokolliert wird das ENDE von stderr**, nicht der Anfang. Die ersten 200
  Zeichen waren ausnahmslos Alembics INFO-Zeilen; die eigentliche Fehlermeldung
  stand dahinter und war abgeschnitten.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

#: Bisher 30 s. Eine Datenmigration, die laenger braucht, wurde damit
#: abgebrochen UND gestempelt.
DEFAULT_UPGRADE_TIMEOUT_SECONDS = 300

#: Wie viel vom Ende der Alembic-Ausgabe ins Protokoll kommt.
STDERR_TAIL_CHARS = 2000

#: Kennzeichen fuer „alembic_version fehlt oder ist leer".
FRISCH = "fresh"


@dataclass(frozen=True)
class Rueckfall:
    """Was der Start nach einem gescheiterten ``upgrade head`` tun soll."""

    #: Datenbank als frisch behandeln: ``create_all`` + ``stamp head``.
    #: Sonst laufen nur die idempotenten Ergaenzungen, Revision bleibt stehen.
    frisch: bool
    #: Protokollstufe: ``WARNING`` fuer den erwarteten Fall (frische DB),
    #: ``ERROR`` fuer die versorgte Anlage, damit es im Fehlerlog auffaellt.
    stufe: int
    #: Fertige Protokollzeile.
    meldung: str


def stderr_ende(text: str | bytes | None, zeichen: int = STDERR_TAIL_CHARS) -> str:
    """Das Ende der Ausgabe — dort steht die Fehlermeldung, nicht am Anfang.

    ``TimeoutExpired.stderr`` kann je nach Python-Version Bytes tragen.
    """
    if not text:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    text = text.strip()
    if len(text) <= zeichen:
        return text
    return "…" + text[-zeichen:]


def upgrade_timeout_seconds(env: dict[str, str] | None = None) -> int:
    """Timeout fuer ``alembic upgrade head``; unbrauchbare Werte fallen auf den Standard."""
    quelle = os.environ if env is None else env
    roh = (quelle.get("ALEMBIC_UPGRADE_TIMEOUT_SECONDS") or "").strip()
    try:
        wert = int(roh)
    except ValueError:
        return DEFAULT_UPGRADE_TIMEOUT_SECONDS
    return wert if wert > 0 else DEFAULT_UPGRADE_TIMEOUT_SECONDS


def entscheide_rueckfall(
    db_zustand: str | None,
    *,
    timeout: bool,
    stderr: str | None = None,
) -> Rueckfall:
    """Als frisch behandeln ja/nein nach einem gescheiterten oder abgebrochenen Upgrade.

    ``db_zustand`` ist :data:`FRISCH`, die Revision aus ``alembic_version`` oder
    ``None``, wenn sie nicht ermittelt werden konnte.
    """
    ende = stderr_ende(stderr)
    if timeout:
        # Der Prozess koennte noch laufen — der Stempel wuerde eine halbe
        # Migration fuer ganz erklaeren. Gilt auch fuer die frische DB.
        return Rueckfall(
            frisch=False,
            stufe=logging.ERROR,
            meldung=(
                "Alembic-Upgrade nach Timeout abgebrochen — NICHT gestempelt, "
                f"alembic_version bleibt bei {db_zustand!r}; naechster Start "
                "versucht es erneut. Timeout ueber "
                "ALEMBIC_UPGRADE_TIMEOUT_SECONDS anheben, wenn die Migration "
                "legitim laenger braucht."
            ),
        )
    if db_zustand == FRISCH:
        return Rueckfall(
            frisch=True,
            stufe=logging.WARNING,
            meldung=(
                "Alembic-Upgrade auf frischer Datenbank fehlgeschlagen — "
                "Tabellen werden aus den Modellen angelegt und auf head "
                f"gestempelt. Ausgabe-Ende: {ende}"
            ),
        )
    return Rueckfall(
        frisch=False,
        stufe=logging.ERROR,
        meldung=(
            "Alembic-Upgrade auf versorgter Datenbank fehlgeschlagen — NICHT "
            f"gestempelt, alembic_version bleibt bei {db_zustand!r}; keine "
            "Tabellen aus den Modellen angelegt, damit die offene Migration "
            "beim naechsten Start noch laufen kann. Anlage laeuft degradiert. "
            f"Ausgabe-Ende: {ende}"
        ),
    )
