"""trigger_create/list/toggle/delete: neue MCP-Werkzeuge, damit der Agent sich
selbst auf Ereignisse statt nur auf Zeitplaene einrichten kann (HANDOVER.md
Schritt 1, "Erlaubnis zur Selbstorganisation"). Die Backend-Endpunkte
(/event-triggers/for-agent*) gab es schon — hier fehlte nur die Werkzeug-Schicht.
"""

import inspect

from app.tools.api_client import OrchestratorAPIClient
from app.tools.definitions import ORCHESTRATOR_TOOL_NAMES
from app.tools.executor import ALWAYS_ALLOWED_TOOLS, CONCURRENT_SAFE_TOOLS, TOOL_CATEGORY_MAP, _CACHEABLE_TOOLS

TRIGGER_TOOLS = {"trigger_create", "trigger_list", "trigger_toggle", "trigger_delete"}


def test_all_four_tools_are_registered():
    assert TRIGGER_TOOLS <= ORCHESTRATOR_TOOL_NAMES


def test_api_client_implements_all_four():
    for name in TRIGGER_TOOLS:
        assert hasattr(OrchestratorAPIClient, name), f"OrchestratorAPIClient.{name} missing"
        assert inspect.iscoroutinefunction(getattr(OrchestratorAPIClient, name))


def test_trigger_list_is_treated_like_list_schedules():
    """Lesen ist immer erlaubt und kann konkurrent laufen — genau wie list_schedules."""
    assert "trigger_list" in ALWAYS_ALLOWED_TOOLS
    assert "trigger_list" in CONCURRENT_SAFE_TOOLS
    assert "trigger_list" in _CACHEABLE_TOOLS


def test_mutating_trigger_tools_are_self_management_not_gated():
    """create/toggle/delete sind Selbstverwaltung, keine Autonomie-Kategorie noetig
    — genau wie create_schedule/manage_schedule schon behandelt werden."""
    for name in ("trigger_create", "trigger_toggle", "trigger_delete"):
        assert name not in TOOL_CATEGORY_MAP
    assert "create_schedule" not in TOOL_CATEGORY_MAP
    assert "manage_schedule" not in TOOL_CATEGORY_MAP


def test_trigger_create_requires_a_prompt_template():
    import asyncio

    client = OrchestratorAPIClient.__new__(OrchestratorAPIClient)
    result = asyncio.run(client.trigger_create({"name": "x"}))
    assert "Error" in result


def test_trigger_toggle_and_delete_require_an_id():
    import asyncio

    client = OrchestratorAPIClient.__new__(OrchestratorAPIClient)
    assert "Error" in asyncio.run(client.trigger_toggle({}))
    assert "Error" in asyncio.run(client.trigger_delete({}))


def test_claude_code_mcp_server_exposes_the_same_tools():
    """agent/mcp/orchestrator-server.mjs ist der zweite Weg (Claude Code statt
    Codex/Custom-LLM) — beide muessen dieselben Werkzeuge tragen, sonst haengt
    die Selbstplanung von der Laufzeit ab, die der Agent zufaellig nutzt."""
    from pathlib import Path

    mjs = (Path(__file__).resolve().parents[1] / "mcp" / "orchestrator-server.mjs").read_text()
    for name in TRIGGER_TOOLS:
        assert f'name: "{name}"' in mjs, f"{name} missing from orchestrator-server.mjs tool list"
        assert f'case "{name}"' in mjs, f"{name} missing a case handler in orchestrator-server.mjs"
    assert "/event-triggers/for-agent" in mjs
