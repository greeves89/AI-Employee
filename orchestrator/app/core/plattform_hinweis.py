"""Einmalwert für Plattform-Hinweise (Sentinel): Nur der Orchestrator kann sie ausstellen.

Der Sentinel faltet seinen Hinweis als Chat-Nachricht mit der Marke ``[Plattform-Sentinel]``
in die laufende Sitzung ein (``chat_auftrag.einreihen(..., plattform=True)``). Bis 1.442
hing die Echtheit daran, dass JEDER andere Weg in die Warteschlange die Marke entschärft —
ein vergessener Weg, und ein fremder Text sähe für das Modell aus wie ein Hinweis der
Plattform. Auf der Agentenseite prüfte niemand etwas.

Jetzt trägt jeder Plattform-Hinweis einen Beleg: HMAC über Agent, Kennung, Zeit und Text,
mit einem Schlüssel je Agent (``encryption.agent_schluessel("sentinel-hinweis", …)``),
für genau eine Gesprächssitzung. Den
Schlüssel hat nur der Agenten-Server (``agent/app/serverwerte.py``, nicht seine Werkzeuge).
Der Agent prüft beim Entnehmen aus der Warteschlange (``agent/app/plattform_hinweis.py``):
Marke nur mit gültigem Beleg, höchstens 10 Minuten alt, jede Kennung nur einmal — sonst
wird die Marke entschärft, BEVOR das Modell den Text sieht.
"""

from __future__ import annotations

import hashlib
import hmac
import time

ZWECK = "sentinel-hinweis"
UMGEBUNG = "SENTINEL_HINWEIS_KEY"      # so heißt der Schlüssel im Agenten-Container
FELD_ZEIT = "plattform_zeit"
FELD_BELEG = "plattform_beleg"


def schluessel(agent_id: str) -> str:
    from app.core.encryption import agent_schluessel

    return agent_schluessel(ZWECK, agent_id)


def beleg(schluessel_wert: str, agent_id: str, kennung: str, zeit: int, text: str, sitzung: str = "") -> str:
    """Gleiche Rechnung wie ``agent/app/plattform_hinweis.beleg`` (beide Tests prüfen dasselbe Beispiel).
    Die Sitzung gehört dazu: Ein Hinweis gilt nur in dem Gespräch, für das er ausgestellt ist."""
    nachricht = f"{agent_id}\n{kennung}\n{zeit}\n{sitzung}\n{text}".encode()
    return hmac.new(schluessel_wert.encode(), nachricht, hashlib.sha256).hexdigest()


def unterschreiben(agent_id: str, payload: dict, jetzt: float | None = None) -> dict:
    """Beleg und Zeit an einen Plattform-Hinweis hängen (``payload`` wird verändert)."""
    zeit = int(jetzt if jetzt is not None else time.time())
    payload[FELD_ZEIT] = zeit
    payload[FELD_BELEG] = beleg(schluessel(agent_id), agent_id, str(payload.get("id") or ""), zeit,
                                str(payload.get("text") or ""), str(payload.get("chat_session_id") or ""))
    return payload


def entfernen(payload: dict) -> dict:
    """Mitgeschickte Beleg-Felder aus einer NICHT-Plattform-Nachricht entfernen."""
    payload.pop(FELD_ZEIT, None)
    payload.pop(FELD_BELEG, None)
    return payload
