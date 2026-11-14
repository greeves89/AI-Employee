"""Vom Sentinel gesperrte Schritte — für Claude Code, Codex und eigene Modelle gleich.

Findet der Sentinel etwas (eine Umlenk-Anweisung in einer Webseite, einen Schlüssel in
einer Datei), sperrt er genau diesen Schritt: Er legt eine agentbezogene Befehlsregel an
(``effect`` "blocked", Name beginnt mit ``SPERRE_PRAEFIX``, im Orchestrator
``core/sentinel_befund.sperrmuster``). Sie steht in der Verwaltung bei den Befehlsregeln
und lässt sich dort wieder löschen.

Die Regel passt auf den KERN eines Schritts, nicht auf den Werkzeugnamen: die URL, den
Dateipfad oder den Befehl. So geht eine Sperre auf eine Webseite nicht daran vorbei, dass
Claude Code sie mit ``WebFetch``, Codex mit einem Befehl und ein eigenes Modell mit
``web_fetch`` holt — und ``curl`` auf dieselbe Adresse trifft sie auch.
"""

from __future__ import annotations

import json
import re

SPERRE_PRAEFIX = "Sentinel-Sperre"
#: Anfang jedes Hinweises, den der Sentinel in die laufende Sitzung einfaltet
#: (orchestrator/app/core/sentinel_befund.MARKE).
MARKE = "[Plattform-Sentinel]"


_MARKE_MUSTER = re.compile(r"\[\s*plattform[\s_\-‐-―]*sentinel", re.IGNORECASE)
#: Volle Breite → ASCII, Zeichen für Zeichen (wie orchestrator/app/core/fremdtext._sichtbar).
_VOLLE_BREITE = {c: c - 0xFEE0 for c in range(0xFF01, 0xFF5F)}


def _sichtbar(text: str) -> str:
    import unicodedata

    t = text.translate(_VOLLE_BREITE)
    return "".join(z for z in t if z in "\n\t" or unicodedata.category(z)[0] != "C")


def hat_marke(text: str) -> bool:
    """Steht die Sentinel-Marke im Text (auch in voller Breite, mit unsichtbaren Zeichen)?"""
    return bool(_MARKE_MUSTER.search(_sichtbar(text)))


def ohne_marke(wert):
    """Die Sentinel-Marke in fremdem Text entschärfen — für Nachrichten anderer Agenten,
    die nie vom Sentinel stammen. Gleiche Regel wie im Orchestrator
    (``fremdtext.ohne_plattformmarke``: volle Breite, unsichtbare Zeichen), und durch
    verschachtelte Felder hindurch. Text ohne Marke bleibt unverändert."""
    if isinstance(wert, dict):
        return {k: ohne_marke(v) for k, v in wert.items()}
    if isinstance(wert, list):
        return [ohne_marke(v) for v in wert]
    if not isinstance(wert, str):
        return wert
    sichtbar = _sichtbar(wert)
    if not _MARKE_MUSTER.search(sichtbar):
        return wert
    return _MARKE_MUSTER.sub("(Plattform-Sentinel", sichtbar)


def regeln_neu_laden(text: str) -> bool:
    """Kommt ein Sentinel-Hinweis an, gelten die zwischengespeicherten Befehlsregeln als
    abgelaufen: Die neue Sperre greift beim nächsten Werkzeugaufruf, nicht erst nach bis
    zu 10 s Zwischenspeicher. Die Liste selbst bleibt als letzter bekannter Stand.
    Ein Nutzer, der die Marke tippt, löst nur ein frühes Neuladen aus — harmlos.
    """
    if not str(text or "").lstrip().startswith(MARKE):
        return False
    from app.tools import executor

    liste, _ = executor._get_command_policies._cache
    executor._get_command_policies._cache = (liste, 0.0)
    return True

_URL = ("url",)
_DATEI = ("file_path", "path", "notebook_path", "filename")
_BEFEHL = ("command", "cmd")


def schritt_kern(werkzeug: str, eingabe) -> str:
    """URL, Dateipfad oder Befehl eines Aufrufs — sonst Werkzeug und Eingabe als Text."""
    e = eingabe if isinstance(eingabe, dict) else {}
    for felder in (_URL, _DATEI, _BEFEHL):
        for feld in felder:
            wert = e.get(feld)
            if isinstance(wert, list):
                wert = " ".join(str(t) for t in wert)
            if isinstance(wert, str) and wert.strip():
                return _ziel_im_befehl(wert.strip()) if felder is _BEFEHL else wert.strip()
    try:
        return f"{werkzeug} {json.dumps(e, sort_keys=True, ensure_ascii=False)}"[:4000]
    except (TypeError, ValueError):
        return f"{werkzeug} {e}"[:4000]


def _ziel_im_befehl(befehl: str) -> str:
    """Nennt ein Befehl genau EINE Datei oder genau EINE Adresse, ist sie der Kern —
    sonst der ganze Befehl. Eine Sperre auf ``cat /a/b.txt`` soll auch ``head`` oder
    ``Read`` auf dieselbe Datei treffen, eine auf ``curl URL`` auch ``WebFetch``."""
    import shlex

    try:
        teile = shlex.split(befehl)
    except ValueError:
        return befehl
    urls = [t for t in teile if t.startswith(("http://", "https://"))]
    pfade = [t for t in teile[1:] if not t.startswith("-") and "/" in t and not t.startswith(("http://", "https://"))]
    if len(urls) == 1 and not pfade:
        return urls[0]
    if len(pfade) == 1 and not urls:
        return pfade[0]
    return befehl


def sperre_fuer(werkzeug: str, eingabe, regeln) -> dict | None:
    """Die Sentinel-Sperre, die auf diesen Aufruf passt — oder None."""
    if not isinstance(regeln, list):
        return None
    kern = schritt_kern(werkzeug, eingabe)
    for regel in regeln:
        if not isinstance(regel, dict) or regel.get("effect") != "blocked":
            continue
        if not str(regel.get("name") or "").startswith(SPERRE_PRAEFIX):
            continue
        try:
            if re.search(str(regel.get("pattern") or ""), kern, re.IGNORECASE):
                return regel
        except re.error:
            continue
    return None


async def gesperrt(werkzeug: str, eingabe) -> str | None:
    """Ablehnungsgrund, wenn der Sentinel diesen Schritt gesperrt hat."""
    from app.tools.executor import _get_command_policies

    try:
        regeln = await _get_command_policies()
    except Exception:  # noqa: BLE001 — ohne Regeln greift die übrige Prüfung wie bisher
        return None
    regel = sperre_fuer(werkzeug, eingabe, regeln)
    if regel is None:
        return None
    grund = str(regel.get("description") or "").strip() or "Vom Sentinel gesperrt."
    return (f"[VOM SENTINEL GESPERRT] {grund} Diesen Schritt nicht wiederholen, auch nicht "
            "mit einem anderen Werkzeug — nimm einen anderen Weg zum Ziel.")
