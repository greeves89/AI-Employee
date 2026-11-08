"""Werkzeug-Paritaet zwischen den eingebauten MCP-Servern und Custom-LLM (#161).

Claude Code und Codex bekommen ihre Werkzeuge von den Servern in ``agent/mcp/``.
Custom-LLM erreicht diese Server nicht, sondern hat ``definitions.py`` und MCP
ueber HTTP. Hier steht, wie jedes Server-Werkzeug dort ankommt, wenn es nicht
denselben Namen traegt. ``tests/test_werkzeug_paritaet.py`` bricht den Build bei
jedem neuen Server-Werkzeug ohne Gegenstueck oder Eintrag.
"""

from __future__ import annotations

#: Server-Werkzeug -> Aktion des Sammelwerkzeugs ``computer_use``.
COMPUTER_USE_AKTIONEN: dict[str, str] = {
    "computer_ax_tree": "ax_tree",
    "computer_click": "mouse_click",
    "computer_close_app": "close_app",
    "computer_drag": "drag",
    "computer_element_action": "element_press",
    "computer_find_element": "find_element",
    "computer_focus_window": "focus_window",
    "computer_get_clipboard": "clipboard_read",
    "computer_key": "key",
    "computer_list_sessions": "list_sessions",
    "computer_list_windows": "list_windows",
    "computer_move": "mouse_move",
    "computer_open_app": "open_app",
    "computer_screenshot": "screenshot",
    "computer_scroll": "mouse_scroll",
    "computer_set_clipboard": "clipboard_write",
    "computer_shell": "shell_run",
    "computer_type": "type",
    "computer_wait_for_element": "wait_for_element",
    **{f"browser_{a}": f"browser_{a}" for a in (
        "capture", "click", "close", "fill", "navigate", "snapshot", "tabs", "wait")},
    **{f"ego_{a}": f"ego_{a}" for a in (
        "capture", "click", "close", "fill", "navigate", "run", "snapshot", "tabs", "wait")},
}

#: Server-Werkzeuge ohne eigenes Gegenstueck, mit Grund.
BEWUSSTE_AUSNAHMEN: dict[str, str] = {
    "computer_use_session": (
        "Merkt sich beim MCP-Server die aktive Sitzung. computer_use bekommt die "
        "session_id stattdessen bei jedem Aufruf."
    ),
}

def server_ohne_werkzeuge(server: list[str], werkzeuge: list[str]) -> list[str]:
    """Eingetragene MCP-Server, von denen das Modell kein einziges Werkzeug sieht.

    Microsoft 365, Gmail und Exchange kommen als solche Server in den Container.
    Ein Eintrag allein heisst also nicht, dass der Agent das Postfach bedienen kann.
    """
    return sorted(s for s in server if not any(w.startswith(f"mcp_{s}_") for w in werkzeuge))
