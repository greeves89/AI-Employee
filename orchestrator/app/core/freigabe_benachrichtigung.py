"""Ist eine Freigabe entschieden, ist ihre Benachrichtigung erledigt.

29.09.2026: In der Glocke standen Freigabe-Fragen mit „Jetzt senden / Abbrechen",
die laengst beantwortet waren — die Freigaben-Seite war leer. Entschieden wird an
sechs Stellen (freigeben, ablehnen, abbrechen, alle verwerfen, Telegram, Ablauf);
keine hat die Benachrichtigung angefasst. Ungelesen zaehlten sie ausserdem als
offene Rueckfragen und loesten die Eskalation „wartet seit ueber 12 Stunden" aus.

Diese Funktion rufen alle Entscheidungsstellen auf, VOR ihrem Commit.
"""
from __future__ import annotations

from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.models.notification import Notification


async def benachrichtigungen_abschliessen(db, freigaben: Iterable) -> int:
    """Benachrichtigungen der uebergebenen (entschiedenen) Freigaben als gelesen
    markieren und die Entscheidung vermerken — die Glocke zeigt sie dann statt der
    Knoepfe. Fehler hier duerfen eine Entscheidung nie verhindern."""
    nach_id = {str(f.id): f for f in freigaben if getattr(f, "id", None) is not None}
    if not nach_id:
        return 0
    # Die ID steht im JSON-Feld — je nach Datenbank als Zahl oder Text. Deshalb
    # die offenen Freigabe-Meldungen der betroffenen Agenten laden und in Python
    # zuordnen, statt im SQL zu vergleichen.
    agenten = {getattr(f, "agent_id", None) for f in nach_id.values()} - {None}
    try:
        abfrage = select(Notification).where(Notification.type == "approval", Notification.read.is_(False))
        if agenten:
            abfrage = abfrage.where(Notification.agent_id.in_(agenten))
        kandidaten = (await db.execute(abfrage)).scalars().all()
    except Exception:  # noqa: BLE001 — Anzeige, kein Betriebsmittel
        return 0
    zeilen = []
    for n in kandidaten:
        f = nach_id.get(str((n.meta or {}).get("approval_id")))
        if f is None:
            continue
        zeilen.append(n)
        status = getattr(f.status, "value", str(f.status))
        n.read = True
        n.meta = {**(n.meta or {}), "entschieden": status, "antwort": (f.user_response or "")[:200]}
        flag_modified(n, "meta")
    return len(zeilen)
