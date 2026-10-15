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

**Und danach wieder wie der MCP-Server.** Der fragt bei jedem ``tools/list``
neu. Frueher hielt dieser Pfad einen Fehlschlag fuer die ganze Lebenszeit des
Agentenprozesses fest — und der lebt lange (``main.py`` startet die Consumer
dauerhaft): Ein kurzer Aussetzer des Orchestrators oder eine spaetere
Freigabe durch den Admin kam nie an (Review zu #812, K3). Jetzt gilt ein
Ergebnis nur begrenzt: ein Erfolg ``GUELTIG_ERFOLG`` Sekunden, ein Fehlschlag
nur ``GUELTIG_FEHLER`` Sekunden. Die Werkzeugkataloge der Handler fragen bei
jedem Aufbau hier nach und bauen sich neu, wenn sich die Freigaben geaendert
haben.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

#: Werkzeugname -> Schluessel in der Antwort von ``/agent-search/capabilities``.
#: Alles, was hier NICHT steht, ist ungefiltert verfuegbar.
FREIGABEPFLICHTIG: dict[str, str] = {
    "news_search": "news",
}

#: Wie lange ein erfolgreich geholter Stand gilt. Eine Admin-Aenderung kommt
#: spaetestens danach an; oefter zu fragen waere bei jedem Zug Verschwendung.
GUELTIG_ERFOLG = 300.0
#: Wie lange ein Fehlschlag gilt. Kurz: Ein Aussetzer soll das Werkzeug nicht
#: dauerhaft verstecken, aber auch nicht jeden Zug eine Anfrage ausloesen.
GUELTIG_FEHLER = 30.0

_zwischenspeicher: dict[str, bool] | None = None
_gueltig_bis: float = 0.0


def _jetzt() -> float:
    return time.monotonic()


async def freigaben(erneuern: bool = False) -> dict[str, bool]:
    """Freigaben des Agenten — zwischengespeichert, aber nur begrenzt gueltig."""
    global _zwischenspeicher, _gueltig_bis
    if _zwischenspeicher is not None and not erneuern and _jetzt() < _gueltig_bis:
        return _zwischenspeicher

    from app.tools.api_client import OrchestratorAPIClient

    client = OrchestratorAPIClient()
    try:
        antwort = await client._request("GET", "/agent-search/capabilities")
        if isinstance(antwort, dict):
            _zwischenspeicher = {k: bool(v) for k, v in antwort.items()}
            _gueltig_bis = _jetzt() + GUELTIG_ERFOLG
        else:
            raise ValueError(f"unerwartete Antwort: {antwort!r}")
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "Freigaben nicht abrufbar (%s) — freigabepflichtige Werkzeuge bleiben "
            "aus, neuer Versuch in %.0f s", e, GUELTIG_FEHLER,
        )
        _zwischenspeicher = {}
        _gueltig_bis = _jetzt() + GUELTIG_FEHLER
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


async def freigegebene_werkzeuge(
    werkzeuge: list[dict], erlaubt: dict[str, bool] | None = None,
) -> list[dict]:
    """``werkzeuge`` ohne die, die dieser Agent nicht nutzen darf."""
    if erlaubt is None:
        erlaubt = await freigaben()
    gefiltert = filtern(werkzeuge, erlaubt)
    entfernt = len(werkzeuge) - len(gefiltert)
    if entfernt:
        logger.info("%d freigabepflichtige(s) Werkzeug(e) ausgeblendet", entfernt)
    return gefiltert
