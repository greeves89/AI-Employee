"""Plattform-Hinweise (Sentinel) beim Entnehmen aus der Chat-Warteschlange prüfen.

Gegenstück zu ``orchestrator/app/core/plattform_hinweis.py``. Die Marke
``[Plattform-Sentinel]`` sieht das Modell nur, wenn die Nachricht einen gültigen Beleg trägt:
HMAC mit dem Schlüssel dieses Agenten (``SENTINEL_HINWEIS_KEY``, nur im Server-Speicher,
``serverwerte``), höchstens 10 Minuten alt, jede Kennung nur einmal. Sonst wird die Marke
entschärft (``schritt_sperre.ohne_marke``) — egal, auf welchem Weg der Text in die
Warteschlange kam.

Die Marke verleiht dem Modell weiterhin keine Befugnis über „diesen Schritt nicht wiederholen,
anderen Weg suchen“ hinaus; die Sperre selbst wirkt technisch. Der Beleg sorgt nur dafür, dass
das Modell die Marke nie an einem Text sieht, der nicht vom Orchestrator stammt.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import threading
import time
from collections import OrderedDict

logger = logging.getLogger(__name__)

UMGEBUNG = "SENTINEL_HINWEIS_KEY"
FELD_ZEIT = "plattform_zeit"
FELD_BELEG = "plattform_beleg"
GUELTIG_S = 600          # so alt darf ein Hinweis höchstens sein
VORLAUF_S = 60           # Uhrenabweichung zwischen Orchestrator und Agent
_MERKEN = 2000           # so viele verbrauchte Kennungen merkt sich der Agent

_verbraucht: "OrderedDict[str, float]" = OrderedDict()
_sperre = threading.Lock()


def beleg(schluessel_wert: str, agent_id: str, kennung: str, zeit: int, text: str, sitzung: str = "") -> str:
    """Gleiche Rechnung wie im Orchestrator (``core/plattform_hinweis.beleg``)."""
    nachricht = f"{agent_id}\n{kennung}\n{zeit}\n{sitzung}\n{text}".encode()
    return hmac.new(schluessel_wert.encode(), nachricht, hashlib.sha256).hexdigest()


def _echt(msg: dict, agent_id: str, jetzt: float) -> tuple[bool, str]:
    from app import serverwerte

    schluessel_wert = serverwerte.get(UMGEBUNG)
    if not schluessel_wert:
        return False, "kein Schlüssel"
    zeit, gegeben = msg.get(FELD_ZEIT), msg.get(FELD_BELEG)
    if not isinstance(zeit, int) or not isinstance(gegeben, str) or not gegeben:
        return False, "ohne Beleg"
    if not (jetzt - GUELTIG_S <= zeit <= jetzt + VORLAUF_S):
        return False, "abgelaufen"
    kennung = str(msg.get("id") or "")
    erwartet = beleg(schluessel_wert, agent_id, kennung, zeit, str(msg.get("text") or ""),
                     str(msg.get("chat_session_id") or ""))
    if not hmac.compare_digest(erwartet, gegeben):
        return False, "Beleg falsch"
    with _sperre:
        if kennung in _verbraucht:
            return False, "schon verwendet"
        _verbraucht[kennung] = jetzt
        while len(_verbraucht) > _MERKEN:
            _verbraucht.popitem(last=False)
    return True, ""


def pruefen(msg: dict, agent_id: str, jetzt: float | None = None) -> dict:
    """Die Nachricht, wie das Modell sie sehen darf: Beleg-Felder entfernt, die Marke nur
    bei echtem, frischem, unverbrauchtem Beleg. Gibt ein NEUES Dict zurück."""
    from app.tools.schritt_sperre import hat_marke, ohne_marke

    msg = dict(msg)
    text = msg.get("text")
    echt = False
    if isinstance(text, str) and hat_marke(text):
        echt, grund = _echt(msg, agent_id, time.time() if jetzt is None else jetzt)
        if not echt:
            # Laut, nicht nur im Protokoll: „kein Schlüssel“ heißt, der Agent ist noch nicht
            # neu erstellt; „abgelaufen“, dass der Hinweis zu lange in der Warteschlange lag.
            print(f"[Agent {agent_id}] Sentinel-Marke ohne gültigen Beleg ({grund}) — entschärft", flush=True)
            logger.warning("Nachricht %s trug die Sentinel-Marke ohne gültigen Beleg (%s) — entschärft",
                           str(msg.get("id") or "?")[:40], grund)
    msg.pop(FELD_ZEIT, None)
    msg.pop(FELD_BELEG, None)
    if echt:
        # Nur der Text ist belegt: andere Felder (Telegram-Anzeigename …) nie mit Marke.
        return {k: (v if k == "text" else ohne_marke(v)) for k, v in msg.items()}
    return ohne_marke(msg)
