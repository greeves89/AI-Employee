"""Kontingent-Bremse: Ist das Abo-Kontingent eines Agenten erschöpft, pausieren seine Zeitpläne.

Ein Abo-Konto (Claude, ChatGPT/Codex) meldet „You've hit your limit · resets 1pm“ bzw.
„try again in 2 hours“. Bis dahin scheitert JEDER Lauf sofort. Erkannt wird das seit #680
(``run_outcome``: „Kontingent erschoepft“) — aber die Zeitpläne liefen weiter: Ein
stündlicher Zeitplan erzeugte bis zur Freigabe einen gescheiterten Lauf nach dem anderen,
ein gestoppter Agent wurde dafür jedes Mal geweckt, und am Morgen stand eine Liste roter
Läufe da, hinter der ein einziger Grund steckte (am 27.–30.08.2026: 23 Läufe).

Jetzt merkt sich der Orchestrator beim ersten solchen Lauf, bis wann das Kontingent
erschöpft ist (aus der Meldung gelesen, sonst eine Stunde), meldet das EINMAL, und der
Zeitplan-Dienst setzt fällige Läufe dieses Agenten auf die Freigabezeit — ein Lauf wird
nachgeholt, sobald wieder Kontingent da ist, statt in der Zwischenzeit sinnlos zu scheitern.

Gilt je Agent: Teilen sich mehrere Agenten ein Konto, scheitert jeder höchstens einmal.
Jede Pause ist höchstens 5 Stunden lang und wird gemeldet (siehe ``HOECHSTENS``).
Chats bremst das nicht — ein Mensch sieht die Meldung des Anbieters direkt.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

GRUND = "Kontingent erschoepft"          # wie run_outcome._SIGNATUREN
VORGABE = timedelta(hours=1)               # Freigabezeit nicht lesbar
MINDESTENS = timedelta(minutes=5)
#: Höchstens 5 Stunden, auch wenn die Meldung „resets Nov 14“ sagt: Der Text kommt aus dem
#: Agenten, und ein manipulierter Agent könnte seine Zeitpläne sonst tagelang stilllegen.
#: Danach versucht der Zeitplan EINEN Lauf; ist das Kontingent noch aus, folgt die nächste
#: Pause. Ein echtes Wochenlimit kostet so einen Lauf je 5 Stunden statt einen je Takt.
HOECHSTENS = timedelta(hours=5)
#: Eine echte Limit-Meldung ist eine kurze Zeile. Ein langer Text, der sie nur ENTHÄLT
#: (Bericht über Limits, eingeschleuster Text), pausiert nichts.
KURZ = 400

_SCHLUESSEL = "kontingent:pause:{}"

_MONATE = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}

# „resets 1pm (Europe/Berlin)“, „resets 3:30pm (America/New_York)“, „resets Nov 10, 9am (Europe/Berlin)“
_RESETS = re.compile(
    r"resets\s+(?:(?P<monat>[A-Za-z]{3})[a-z]*\.?\s+(?P<tag>\d{1,2}),?\s+(?:at\s+)?)?"
    r"(?P<stunde>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>[ap]\.?m\.?)"
    r"(?:\s*\((?P<zone>[A-Za-z_]+/[A-Za-z_/+-]+|UTC)\))?", re.I)
# „try again in 2 hours 5 minutes“, „in 4 days 3 hours“, „in 45 minutes“
_IN = re.compile(r"(?:try again|retry|resets?)\s+in\s+((?:\d+\s*(?:days?|d|hours?|hrs?|h|minutes?|mins?|m)\s*,?\s*(?:and\s+)?)+)", re.I)
_TEIL = re.compile(r"(\d+)\s*(days?|d|hours?|hrs?|h|minutes?|mins?|m)\b", re.I)


def _zone(name: str | None):
    from zoneinfo import ZoneInfo
    try:
        return ZoneInfo(name) if name else timezone.utc
    except Exception:  # noqa: BLE001 — unbekannte Zone: lieber UTC als gar nichts
        return timezone.utc


def bis_wann(text: str | None, jetzt: datetime) -> datetime:
    """Bis wann das Kontingent erschöpft ist — aus der Meldung, sonst ``jetzt + 1 h``.

    Ergebnis liegt immer zwischen 5 Minuten und 5 Stunden nach ``jetzt`` (UTC).
    """
    jetzt = jetzt.astimezone(timezone.utc)
    text = text or ""
    ziel: datetime | None = None

    m = _IN.search(text)
    if m:
        dauer = timedelta()
        for zahl, einheit in _TEIL.findall(m.group(1)):
            e = einheit.lower()
            dauer += (timedelta(days=int(zahl)) if e.startswith("d")
                      else timedelta(hours=int(zahl)) if e.startswith("h")
                      else timedelta(minutes=int(zahl)))
        if dauer:
            ziel = jetzt + dauer

    m = _RESETS.search(text) if ziel is None else None
    if m:
        zone = _zone(m.group("zone"))
        lokal = jetzt.astimezone(zone)
        stunde = int(m.group("stunde")) % 12 + (12 if m.group("ampm").lower().startswith("p") else 0)
        minute = int(m.group("minute") or 0)
        try:
            if m.group("monat") and m.group("monat").lower()[:3] in _MONATE:
                kandidat = lokal.replace(month=_MONATE[m.group("monat").lower()[:3]], day=int(m.group("tag")),
                                         hour=stunde, minute=minute, second=0, microsecond=0)
                if kandidat < lokal:
                    kandidat = kandidat.replace(year=kandidat.year + 1)
            else:
                kandidat = lokal.replace(hour=stunde, minute=minute, second=0, microsecond=0)
                if kandidat <= lokal:
                    kandidat += timedelta(days=1)
            ziel = kandidat.astimezone(timezone.utc)
        except ValueError:
            ziel = None

    if ziel is None:
        ziel = jetzt + VORGABE
    return min(max(ziel, jetzt + MINDESTENS), jetzt + HOECHSTENS)


async def pausieren(redis, agent_id: str, bis: datetime, grund: str = GRUND) -> bool:
    """Pause merken. ``True``, wenn sie NEU ist (dann einmal melden), sonst ``False``.

    Eine schon laufende Pause wird nur verlängert, nie verkürzt.
    """
    client = getattr(redis, "client", None)
    if client is None or not agent_id:
        return False
    schluessel = _SCHLUESSEL.format(agent_id)
    try:
        alt = await pausiert_bis(redis, agent_id)
        if alt is not None and alt >= bis:
            return False
        await client.set(schluessel, json.dumps({"bis": bis.isoformat(), "grund": grund}),
                         exat=int(bis.timestamp()))
        return alt is None
    except Exception:  # noqa: BLE001 — die Bremse darf das Abschließen eines Laufs nie stören
        logger.warning("Kontingent-Pause für Agent %s nicht gemerkt", agent_id, exc_info=True)
        return False


async def pausiert_bis(redis, agent_id: str) -> datetime | None:
    """Bis wann die Zeitpläne des Agenten pausieren — ``None``, wenn nicht."""
    client = getattr(redis, "client", None)
    if client is None or not agent_id:
        return None
    try:
        roh = await client.get(_SCHLUESSEL.format(agent_id))
        if not roh:
            return None
        bis = datetime.fromisoformat(json.loads(roh)["bis"])
        return bis if bis > datetime.now(timezone.utc) else None
    except Exception:  # noqa: BLE001 — im Zweifel nicht bremsen
        return None


async def aufheben(redis, agent_id: str) -> None:
    """Pause beenden (etwa nach neuer Anmeldung oder einem erfolgreichen Lauf)."""
    client = getattr(redis, "client", None)
    if client is None:
        return
    try:
        await client.delete(_SCHLUESSEL.format(agent_id))
    except Exception:  # noqa: BLE001
        pass
