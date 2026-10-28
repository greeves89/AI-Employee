"""Dateien am Projekt: hochladen, auslesen, durchsuchbar machen.

Das Projektwissen (``projekt_wissen``) ist eine Text-Ablage mit git-Verlauf —
bewusst kein Binärspeicher (``vault.write_file`` nimmt nur .md/.txt). Darum:

* das **Original** liegt neben der Ablage in ``<ablage>.dateien/<name>`` (selbes
  Volume, kein git) und lässt sich herunterladen;
* der **ausgelesene Text** (PDF, Word, Excel, Text) kommt als Seite
  ``dateien/<name>.md`` ins Projektwissen — mit Verlauf und im Suchindex, so dass
  Agenten den Inhalt finden, ohne dass ihn jemand abtippt.

Namen werden bereinigt (keine Pfade, keine versteckten Dateien), Größe und Art
sind begrenzt. Wer was darf, prüft die API (``api/teams.py``) wie beim übrigen
Projektwissen.
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.second_brain import SecondBrain

log = logging.getLogger(__name__)

#: Erlaubte Arten. Bilder werden abgelegt, aber nicht ausgelesen (kein OCR).
ENDUNGEN = {".pdf", ".docx", ".xlsx", ".txt", ".md", ".csv", ".json", ".png", ".jpg", ".jpeg"}
TEXT_ENDUNGEN = {".txt", ".md", ".csv", ".json"}
MAX_BYTES = 20 * 1024 * 1024
#: So viel ausgelesener Text kommt höchstens auf die Seite (der Rest steht im Original).
MAX_TEXT = 200_000
SEITEN_ORDNER = "dateien"

_ERLAUBTE_ZEICHEN = re.compile(r"[^\w .()\-]+", re.UNICODE)


class DateiFehler(ValueError):
    """Name, Art oder Größe passen nicht — mit deutscher Meldung."""


def _ablage(brain: SecondBrain) -> str:
    return brain.host_path.rstrip("/") + ".dateien"


def sicherer_name(name: str) -> str:
    """Nur der Dateiname, ohne Pfad und Sonderzeichen — oder ``DateiFehler``."""
    name = unicodedata.normalize("NFC", os.path.basename((name or "").replace("\\", "/"))).strip()
    name = _ERLAUBTE_ZEICHEN.sub("_", name).strip(" .")
    stamm, endung = os.path.splitext(name)
    endung = endung.lower()
    if not stamm or endung not in ENDUNGEN:
        raise DateiFehler(f"Diese Dateiart geht nicht. Erlaubt: {', '.join(sorted(ENDUNGEN))}.")
    return f"{stamm[:120]}{endung}"


def _freier_name(ordner: str, name: str) -> str:
    stamm, endung = os.path.splitext(name)
    kandidat, n = name, 2
    while os.path.exists(os.path.join(ordner, kandidat)):
        kandidat, n = f"{stamm} ({n}){endung}", n + 1
    return kandidat


def _pfad(brain: SecondBrain, name: str) -> str:
    """Absoluter Pfad einer vorhandenen Datei — eingesperrt in die Ablage."""
    ordner = os.path.realpath(_ablage(brain))
    ziel = os.path.realpath(os.path.join(ordner, os.path.basename(name or "")))
    if os.path.dirname(ziel) != ordner or not os.path.isfile(ziel):
        raise FileNotFoundError(name)
    return ziel


def _text(inhalt: bytes, name: str) -> str | None:
    from app.core.dokument_text import text_auslesen

    endung = os.path.splitext(name)[1].lower()
    if endung in TEXT_ENDUNGEN:
        return inhalt.decode("utf-8", errors="replace")[:MAX_TEXT]
    return text_auslesen(inhalt, name, "", max_chars=MAX_TEXT)


def _groesse(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MB" if n >= 1024 * 1024 else f"{max(1, n // 1024)} KB"


def seite_von(name: str) -> str:
    return f"{SEITEN_ORDNER}/{name}.md"


async def ablegen(db: AsyncSession, brain: SecondBrain, name: str, inhalt: bytes, *, wer: str) -> dict:
    """Datei ablegen und ihren Text als Seite ins Projektwissen schreiben."""
    from app.core import projekt_wissen

    name = sicherer_name(name)
    if not inhalt:
        raise DateiFehler("Die Datei ist leer.")
    if len(inhalt) > MAX_BYTES:
        raise DateiFehler(f"Die Datei ist größer als {MAX_BYTES // 1024 // 1024} MB.")
    ordner = _ablage(brain)
    os.makedirs(ordner, exist_ok=True)
    name = _freier_name(ordner, name)
    ziel = os.path.join(ordner, name)
    tmp = ziel + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(inhalt)
    os.replace(tmp, ziel)

    text = _text(inhalt, name)
    seite = seite_von(name)
    kopf = (f"# {name}\n\n*Hochgeladene Datei ({_groesse(len(inhalt))}, {wer}). Der Inhalt unten ist "
            "automatisch ausgelesen; die Datei selbst liegt im Projekt unter „Wissen → Dateien“.*\n\n")
    await projekt_wissen.schreiben(db, brain, seite, kopf + (text or "(Kein Text auslesbar — z. B. ein Bild.)") + "\n",
                                   wer=wer)
    return {"name": name, "bytes": len(inhalt), "seite": seite, "text_ausgelesen": bool(text)}


def liste(brain: SecondBrain) -> list[dict]:
    ordner = _ablage(brain)
    if not os.path.isdir(ordner):
        return []
    aus = []
    for name in sorted(os.listdir(ordner), key=str.lower):
        pfad = os.path.join(ordner, name)
        if name.startswith(".") or name.endswith(".tmp") or not os.path.isfile(pfad):
            continue
        st = os.stat(pfad)
        aus.append({"name": name, "bytes": st.st_size, "seite": seite_von(name),
                    "geaendert": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat()})
    return aus


def pfad_zum_herunterladen(brain: SecondBrain, name: str) -> str:
    return _pfad(brain, name)


async def loeschen(db: AsyncSession, brain: SecondBrain, name: str) -> None:
    """Original und ausgelesene Seite entfernen (auch aus dem Suchindex)."""
    from app.core import vault

    os.remove(_pfad(brain, name))
    seite = seite_von(os.path.basename(name))
    try:
        vault.delete_file(brain.host_path, seite)
    except FileNotFoundError:
        pass
    try:
        from app.services import vault_indexer

        await vault_indexer.remove_file(db, brain.label, seite)
        await db.commit()
    except Exception:  # noqa: BLE001 — der Index raeumt beim naechsten Lauf ohnehin auf
        await db.rollback()
        log.warning("Suchindex fuer geloeschte Projektdatei nicht bereinigt", exc_info=True)
