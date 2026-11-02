"""Eine Token-Erneuerung der Codex-CLI zurueck an den Orchestrator melden.

Der ChatGPT-Refresh-Token ist einmalig. Erneuert die CLI den Zugang, schreibt
sie den neuen Token in ``CODEX_HOME/auth.json`` — nur dorthin. Der Container
wurde aber mit einer **Abschrift** aus der Datenbank gestartet: ohne diesen
Rueckweg ist die Erneuerung mit dem Container weg, der naechste Start spielt die
verbrauchte Fassung erneut ein, und der Anbieter antwortet ab da bei jedem Start
mit ``refresh_token_reused`` (Issue #646).

Gemeldet wird nur der **eigene** Zugang des Besitzers. Der gemeinsame Zugang der
Anlage wird zentral im Orchestrator gepflegt und bleibt hier unangetastet.
"""

import hashlib
import logging
import os

from app.config import settings

logger = logging.getLogger(__name__)

_source: str = ""
_last_hash: str = ""


def auth_path() -> str:
    return os.path.join(os.environ.get("CODEX_HOME", "/home/agent/.codex"), "auth.json")


def _hash() -> str:
    try:
        with open(auth_path(), "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return ""


def ist_platzhalter() -> bool:
    """Liegt der Zugang hinter dem Egress-Proxy (Platzhalter statt Token)?"""
    try:
        with open(auth_path(), encoding="utf-8") as fh:
            return "aie-geheim-" in fh.read()
    except OSError:
        return False


def platzhalter_frisch_halten() -> None:
    """Hinter dem Proxy darf die CLI nie selbst erneuern wollen — ihr Erneuerungs-Token
    ist ein Platzhalter, der nie aufgelöst wird. Sie erneuert, wenn ``last_refresh`` alt
    ist; deshalb steht dort vor jedem Lauf „jetzt“. Das echte Token hält der
    Orchestrator frisch, der Proxy setzt immer das aktuelle ein."""
    import json
    from datetime import datetime, timezone

    if not ist_platzhalter():
        return
    try:
        with open(auth_path(), encoding="utf-8") as fh:
            daten = json.load(fh)
        daten["last_refresh"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with open(auth_path(), "w", encoding="utf-8") as fh:
            json.dump(daten, fh)
    except (OSError, ValueError) as e:
        logger.warning("Codex auth.json (Platzhalter) nicht aufgefrischt: %s", e)


def record_start(source: str) -> None:
    """Merken, woher der Zugang kam und wie er beim Start aussah.

    ``source`` ist ``own`` fuer den eigenen Zugang des Besitzers, sonst etwas
    anderes (gemeinsame Datei, API-Schluessel, kein Zugang).
    """
    global _source, _last_hash
    _source = source
    _last_hash = _hash()


async def push_if_rotated() -> bool:
    """Hat die CLI den Zugang erneuert, ihn zurueckschreiben lassen.

    Faellt still aus, wenn nichts zu tun ist. Ein Fehler hier darf einen Lauf nie
    scheitern lassen — beim naechsten Lauf wird es erneut versucht, weil der
    Vergleichswert erst nach einer erfolgreichen Uebernahme fortgeschrieben wird.
    """
    global _last_hash
    if _source != "own" or ist_platzhalter():
        return False  # nie einen Platzhalter als „erneuerten Zugang“ zurückschreiben
    current = _hash()
    if not current or current == _last_hash:
        return False

    import json

    import httpx

    try:
        with open(auth_path(), encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError) as e:
        logger.warning("Codex auth.json nach Erneuerung nicht lesbar: %s", e)
        return False

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{settings.orchestrator_url}/api/v1/agent-codex-auth",
                json={"auth_json": payload},
                headers={
                    "X-Agent-ID": settings.agent_id,
                    "Authorization": f"Bearer {settings.agent_token}",
                },
            )
    except Exception as e:  # noqa: BLE001 — Netzfehler: naechster Lauf versucht es erneut
        logger.warning("Codex-Erneuerung konnte nicht gemeldet werden: %s", e)
        return False

    if resp.status_code >= 400:
        # Kein Token, kein Auszug aus der Antwort ins Log — nur der Grund.
        logger.warning(
            "Codex-Erneuerung abgelehnt (HTTP %s): %s",
            resp.status_code, resp.json().get("detail", "") if resp.headers.get(
                "content-type", "").startswith("application/json") else "",
        )
        return False

    _last_hash = current
    logger.info("Codex-Erneuerung an den Orchestrator uebergeben")
    return True
