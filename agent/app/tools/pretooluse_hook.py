"""Claude Code PreToolUse hook decision logic (issue #197, Teil 2).

Claude Code agents run their OWN native tool implementations (Bash, Write,
Edit, ...) plus MCP-server tools — none of that passes through
``ToolExecutor.execute()`` (that class only runs for ``mode=custom_llm``).
Containers are launched with ``--dangerously-skip-permissions``, and the
pre-existing ``bash-approval-server.mjs`` MCP tool is only a PARALLEL, opt-in
``Bash`` tool the model can simply not call — the native, unrestricted one
stays available regardless. In practice the whole autonomy matrix / command
policy system was unenforceable for this runtime.

Fix: an HTTP-type PreToolUse hook (Claude Code docs confirm this fires for
EVERY tool call, built-in and MCP alike, is NOT bypassed by
``--dangerously-skip-permissions``, and can deny via
``permissionDecision: "deny"``) registered in the agent's own
``.claude/settings.json`` (see ``agent_manager.py::_claude_pretooluse_settings_json``),
pointed at this same container's own health server
(``agent/app/health.py::pretooluse_hook_handler``).

Deliberately reuses ``executor.py``'s ``ALWAYS_ALLOWED_TOOLS``/
``TOOL_CATEGORY_MAP``/``_get_allowed_categories()`` rather than a second,
Claude-Code-specific table — those two runtimes' MCP tool surface is the
SAME set of capabilities (``test_harness_capability_parity.py`` enforces
this), so one classification, applied twice with two small naming
translations (native Claude Code names, and the ``mcp__<server>__<name>``
prefix), stays in sync by construction instead of by habit.
"""
from __future__ import annotations

from app.tools.executor import ALWAYS_ALLOWED_TOOLS, TOOL_CATEGORY_MAP, _get_allowed_categories

# Claude Code's OWN built-in tool names (capitalized, distinct from the
# lowercase snake_case names ``definitions.py``/``executor.py`` use for the
# custom_llm runtime). Read-only/meta tools with no external effect of their
# own (reading shell output, killing a shell Claude Code itself started,
# delegating to a subagent, offering a slash command, todo bookkeeping) are
# always allowed, same risk class as their custom_llm counterparts
# (read_file/glob/grep/web_fetch/web_search/update_todos/create_task/
# list_tasks). The four that write or execute get the SAME category strings
# TOOL_CATEGORY_MAP already uses, so one allow-list drives both runtimes.
_NATIVE_ALWAYS_ALLOWED = frozenset({
    "Read", "Glob", "Grep", "WebFetch", "WebSearch", "TodoWrite",
    "BashOutput", "KillShell", "Task", "SlashCommand",
    # Die AKTUELLEN Namen derselben Werkzeuge (Claude Code hat sie umbenannt)
    # und die seitdem dazugekommenen Meta-Werkzeuge. Am 2026-09-17 sperrte die
    # erste Fassung mit nur `Task`/`SlashCommand` auf einer laufenden Anlage
    # jeden Subagenten, jede Skill-Anweisung und das Nachladen zurueckgestellter
    # Werkzeugschemata — obwohl der Agent "Alles erlaubt" (L4) hatte.
    "Agent",       # = Task: Subagent; laeuft selbst unter genau diesem Hook
    "Skill",       # = SlashCommand: laedt nur Anweisungen
    "ToolSearch",  # laedt nur Schemata; jeder Aufruf danach wird einzeln geprueft
    "ListAgents", "TaskOutput", "TaskStop",
    "SendMessage",  # wie `send_message` in ALWAYS_ALLOWED_TOOLS
    "Workflow",     # orchestriert Subagenten — dieselbe Klasse wie Agent
    "ScheduleWakeup", "CronCreate", "CronList", "CronDelete",  # wie `create_schedule`
    "ReportFindings",  # reine Ausgabe an die Oberflaeche
    "ListMcpResourcesTool", "ReadMcpResourceTool", "ReadMcpResourceDirTool",
})
_NATIVE_CATEGORY_MAP: dict[str, str] = {
    "Bash": "shell_exec",
    "Monitor": "shell_exec",  # fuehrt wiederholt ein Shell-Kommando aus
    "Write": "file_write",
    "Edit": "file_write",
    "NotebookEdit": "file_write",
    "EnterWorktree": "file_write",  # legt einen git-Worktree im Dateisystem an
    "ExitWorktree": "file_write",
}

# Plattform-eigene MCP-Server, deren Werkzeuge NICHT in den custom_llm-Tabellen
# stehen (dort gibt es sie nicht): `read-logs` (eigene Containerlogs, nur
# lesend) und der Desktop-Server, dessen Werkzeuge `computer_<aktion>` heissen
# und in der Summe genau das sind, was `computer_use` in TOOL_CATEGORY_MAP ist.
_MCP_BARE_ALWAYS_ALLOWED = frozenset({"read_logs"})
_MCP_BARE_PREFIX_CATEGORY: dict[str, str] = {
    "computer_": TOOL_CATEGORY_MAP["computer_use"],
}


def _decide_for(category: str, *, label: str) -> dict:
    allowed = _get_allowed_categories()
    if allowed is not None and category not in allowed:
        return _deny(
            f"Werkzeug '{label}' braucht Kategorie '{category}', die nicht in der "
            "aktuellen Freigabeliste steht. `request_approval` zuerst aufrufen."
        )
    return _allow()


def decide(tool_name: str, tool_input: dict | None = None) -> dict:
    """Return a PreToolUse hook response for one tool call.

    ``tool_input`` is accepted (and reserved) for a future finer-grained,
    per-command decision (mirroring ``command_policy`` pattern matching) —
    today the decision is purely per-tool-category, same granularity as the
    custom_llm executor. An unrecognized tool name (neither a known native
    tool nor an ``mcp__<server>__<name>`` tool whose bare name is in the
    shared tables) is treated as needing approval, not silently allowed —
    exactly the bug #197 closed for the custom_llm runtime.
    """
    if tool_name in _NATIVE_ALWAYS_ALLOWED:
        return _allow()
    if tool_name in _NATIVE_CATEGORY_MAP:
        return _decide_for(_NATIVE_CATEGORY_MAP[tool_name], label=tool_name)

    if tool_name.startswith("mcp__"):
        parts = tool_name.split("__", 2)
        bare = parts[2] if len(parts) == 3 else ""
        if bare in ALWAYS_ALLOWED_TOOLS or bare in _MCP_BARE_ALWAYS_ALLOWED:
            return _allow()
        if bare in TOOL_CATEGORY_MAP:
            return _decide_for(TOOL_CATEGORY_MAP[bare], label=tool_name)
        for prefix, category in _MCP_BARE_PREFIX_CATEGORY.items():
            if bare.startswith(prefix):
                return _decide_for(category, label=tool_name)

    # Unbekannt. Ohne Freigabeliste (keine Regeln, oder die L4-Regel "Alles
    # erlaubt" — beides liefert None) gibt es NICHTS, wogegen ein unbekanntes
    # Werkzeug verstossen koennte: derselbe "keine Regeln = keine
    # Einschraenkung"-Vertrag, der oben schon Bash und Write durchlaesst. Erst
    # eine konfigurierte Freigabeliste macht Unbekanntes zum Freigabefall —
    # das ist die Luecke, die #197 fuer die custom_llm-Laufzeit geschlossen hat.
    if _get_allowed_categories() is None:
        return _allow()
    return _deny(
        f"Unbekanntes Werkzeug '{tool_name}' — nicht in der Autonomie-Zuordnung "
        "verzeichnet. Bitte `request_approval` verwenden."
    )


def _allow() -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
        }
    }


def _deny(reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
