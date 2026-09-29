"""Was von einem Subagenten oder einer Delegation im Verlauf bleiben muss.

Werkzeugaufrufe werden im Verlauf auf 200 Zeichen gekuerzt gespeichert — als
JSON meist nicht mehr lesbar. Fuer Helfer faellt dabei weg, was sie ausmacht:
Titel, Auftrag, Ziel. Nach dem Neuladen stand dann „Delegierter Auftrag — an
anderen Agenten" mit „(kein Auftragstext übermittelt)" (28./29.09.2026, auf
einer Kundenanlage). Bis dahin bekamen nur eigene Subagenten (Agent/Task) ihre
Felder gesondert; Delegationen an andere Agenten nicht.

Eine Funktion fuer alle Laufzeiten (Claude Code, eigenes LLM), damit beide
dasselbe ablegen. Gegenstueck im Frontend: ``subagentAusInput`` in chat.tsx.
"""
from __future__ import annotations

#: Eigene Subagenten der Laufzeit (Claude Code).
EIGENE = {"Agent", "Task"}
#: Delegation an andere Agenten ueber den Orchestrator (MCP-Werkzeuge).
DELEGATION = {"create_task", "create_task_batch", "delegate_and_wait"}
AUFTRAG_GRENZE = 4000


def _kurzname(werkzeug: str) -> str:
    # "mcp__orchestrator__create_task" -> "create_task"
    return (werkzeug or "").split("__")[-1]


def subagent_felder(werkzeug: str, eingabe: dict | None) -> dict | None:
    """Die Kernfelder eines Helfers — oder ``None`` fuer gewoehnliche Werkzeuge."""
    eingabe = eingabe if isinstance(eingabe, dict) else {}
    if werkzeug in EIGENE:
        return {
            "description": str(eingabe.get("description", ""))[:200],
            "subagent_type": eingabe.get("subagent_type"),
            "run_in_background": bool(eingabe.get("run_in_background")),
            "prompt": str(eingabe.get("prompt", ""))[:AUFTRAG_GRENZE],
        }
    kurz = _kurzname(werkzeug)
    if kurz not in DELEGATION:
        return None
    stapel = eingabe.get("tasks") if isinstance(eingabe.get("tasks"), list) else None
    if stapel:
        titel = [str(t.get("title", "")) for t in stapel if isinstance(t, dict) and t.get("title")]
        beschreibung = titel[0] if len(titel) == 1 else f"{len(stapel)} Aufträge delegiert"
        auftrag = "\n\n".join(
            f"{i + 1}. {t.get('title', '')}\n{t.get('prompt', '')}"
            for i, t in enumerate(stapel) if isinstance(t, dict)
        )
        ziel = None
    else:
        beschreibung = str(eingabe.get("title") or "Delegierter Auftrag")
        auftrag = str(eingabe.get("prompt", ""))
        ziel = eingabe.get("agent_id")
    return {
        "description": beschreibung[:200],
        # Wohin — die Oberflaeche schreibt „an anderen Agenten" selbst davor.
        "subagent_type": f"an {str(ziel)[:8]}" if ziel else None,
        "run_in_background": kurz != "delegate_and_wait",
        "prompt": auftrag[:AUFTRAG_GRENZE],
    }
