"""Freigaben, die an genau EINE Handlung gebunden sind — und die der Server ausführt.

Bisher galt bei „Freigabe“ in der Rechte-Matrix: Der Agent fragt (``request_approval``),
der Mensch gibt frei, der Agent handelt danach selbst. Der Server sah nicht, ob die
Handlung danach dieselbe war, für die freigegeben wurde (P1-13/P2-17 im
Bedrohungsmodell). Für Mails war das schon gelöst (``core/mail_freigabe``); hier steht
dasselbe Muster für jede Handlung, die der Server selbst ausführen kann:

1. Ein Endpunkt mit Wirkung nach außen sieht „Freigabe“ und ruft ``anfragen`` auf.
   Die genaue Handlung (Ziel, Inhalt) landet in der Freigabe, dazu ein Fingerabdruck.
   Der Agent bekommt 202 und nichts, was er später einlösen könnte.
2. Der Mensch sieht in der Freigabe genau diese Handlung (Ansicht „handlung“).
3. Beim Freigeben (``api/approvals.approve_request``) prüft ``ausfuehren`` den
   Fingerabdruck und führt die Handlung EINMAL aus. Die Zeilensperre der Freigabe
   verhindert eine doppelte Ausführung.

Neue Handlungen melden sich mit ``@ausfuehrer("art")`` an — die Arten stehen damit
auch in ``api/approvals.SERVER_ARTEN``: Ein Agent kann sie nicht selbst anlegen.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable

from fastapi import HTTPException

logger = logging.getLogger(__name__)

#: Art → Ausführung (db, daten, agent_id) → kurzer Bericht für Agent und Protokoll.
AUSFUEHRER: dict[str, Callable[..., Awaitable[str]]] = {}


def ausfuehrer(art: str):
    def anmelden(fn):
        AUSFUEHRER[art] = fn
        return fn
    return anmelden


def fingerabdruck(art: str, daten: dict) -> str:
    return hashlib.sha256(json.dumps({"art": art, "daten": daten}, sort_keys=True,
                                     ensure_ascii=False).encode()).hexdigest()


async def anfragen(db, agent_id: str, art: str, daten: dict, *, titel: str,
                   felder: list[tuple[str, str]], werkzeug: str, task_id: str | None = None) -> dict:
    """Die Handlung zur Freigabe stellen. Liefert die Antwort für den Agenten (202)."""
    from app.api.approvals import ApprovalRequest, anfrage_anlegen

    if art not in AUSFUEHRER:
        raise RuntimeError(f"Keine Ausführung für {art!r} angemeldet")
    ansicht = {"titel": titel, "felder": [{"name": n, "wert": str(w)[:3000]} for n, w in felder]}
    erg = await anfrage_anlegen(db, agent_id, ApprovalRequest(
        tool=werkzeug, kind=art, risk_level="medium", reasoning=titel, task_id=task_id,
        input={"daten": daten, "fingerabdruck": fingerabdruck(art, daten)},
        view={"name": "handlung", "data": ansicht},
    ))
    return {
        "status": "wartet_auf_freigabe",
        "approval_id": erg.get("approval_id"),
        "hinweis": (f"Nicht ausgeführt: „{titel}“ wartet auf Freigabe. Nach der Freigabe führt die "
                    "Plattform genau diese Handlung aus — nicht noch einmal selbst versuchen."),
    }


async def ausfuehren(db, approval) -> str | None:
    """Beim Freigeben: die gebundene Handlung genau einmal ausführen. None = keine solche."""
    meta = approval.meta or {}
    art = meta.get("kind")
    fn = AUSFUEHRER.get(art)
    if fn is None:
        return None
    eingabe = meta.get("input") or {}
    daten = eingabe.get("daten") or {}
    if eingabe.get("fingerabdruck") != fingerabdruck(art, daten):
        # Die Zeile wurde nach dem Anlegen verändert — nichts ausführen.
        raise HTTPException(status_code=409, detail="Die Handlung wurde nach der Anfrage verändert — nicht ausgeführt.")
    try:
        return await fn(db, daten, approval.agent_id)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — die Freigabe bleibt dann offen, der Mensch kann es erneut versuchen
        logger.exception("Gebundene Handlung %s (Freigabe %s) gescheitert", art, approval.id)
        raise HTTPException(status_code=502, detail=f"Ausführung gescheitert: {e}") from None
