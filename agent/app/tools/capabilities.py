"""Welche Werkzeuge dieser Agent tatsaechlich angeboten bekommt.

``definitions.py`` ist ein statischer Katalog: Er beschreibt, was es GIBT,
nicht was dieser Agent DARF. Fuer Faehigkeiten, die ein Admin freigibt, muss
vor der Uebergabe ans Modell gefiltert werden.

**Warum es dieses Modul gibt — Harness-Paritaet.** Claude Code und Codex
bekommen den Filter ueber den MCP-Server (``sucheFreigaben()`` in
``agent/mcp/orchestrator-server.mjs``). Custom-LLM und die Sprachfront lesen
``TOOL_DEFINITIONS`` dagegen direkt. Ohne diesen Weg saehe das Modell dort ein
Werkzeug, das beim Aufruf mit 403 antwortet — keine Sicherheitsluecke, aber
eine Faehigkeit, die sich je nach Laufzeit anders verhaelt. Genau das soll die
Paritaet verhindern.

Bei einem Fehlschlag wird die betroffene Faehigkeit ausgeblendet, nicht
angeboten — dieselbe Richtung wie im MCP-Server, damit sich beide Pfade auch
im Stoerfall gleich verhalten.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Werkzeugname -> Schluessel in der Antwort von ``/agent-search/capabilities``.
#: Alles, was hier NICHT steht, ist ungefiltert verfuegbar.
FREIGABEPFLICHTIG: dict[str, str] = {
    "news_search": "news",
}

_zwischenspeicher: dict[str, bool] | None = None


async def freigaben(erneuern: bool = False) -> dict[str, bool]:
    """Freigaben des Agenten, einmal je Prozess geholt.

    Der Wert aendert sich nur, wenn ein Admin eine Rolle anpasst — ein
    Abruf je Aufgabe waere Verschwendung, und der Agentenprozess lebt
    ohnehin nicht lange.
    """
    global _zwischenspeicher
    if _zwischenspeicher is not None and not erneuern:
        return _zwischenspeicher

    from app.tools.api_client import OrchestratorAPIClient

    client = OrchestratorAPIClient()
    try:
        antwort = await client._request("GET", "/agent-search/capabilities")
        if isinstance(antwort, dict):
            _zwischenspeicher = {k: bool(v) for k, v in antwort.items()}
        else:
            raise ValueError(f"unerwartete Antwort: {antwort!r}")
    except Exception as e:  # noqa: BLE001
        logger.warning("Freigaben nicht abrufbar (%s) — freigabepflichtige Werkzeuge bleiben aus", e)
        _zwischenspeicher = {}
    finally:
        try:
            await client.close()
        except Exception:  # noqa: BLE001
            pass
    return _zwischenspeicher


def filtern(werkzeuge: list[dict], erlaubt: dict[str, bool]) -> list[dict]:
    """Aus ``werkzeuge`` alles entfernen, was nicht freigegeben ist."""
    def durchlassen(t: dict) -> bool:
        name = (t.get("function") or {}).get("name") or t.get("name") or ""
        schluessel = FREIGABEPFLICHTIG.get(name)
        return True if schluessel is None else bool(erlaubt.get(schluessel))

    return [t for t in werkzeuge if durchlassen(t)]


async def freigegebene_werkzeuge(werkzeuge: list[dict]) -> list[dict]:
    """``werkzeuge`` ohne die, die dieser Agent nicht nutzen darf."""
    gefiltert = filtern(werkzeuge, await freigaben())
    entfernt = len(werkzeuge) - len(gefiltert)
    if entfernt:
        logger.info("%d freigabepflichtige(s) Werkzeug(e) ausgeblendet", entfernt)
    return gefiltert
