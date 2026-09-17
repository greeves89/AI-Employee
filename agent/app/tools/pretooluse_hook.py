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
})
_NATIVE_CATEGORY_MAP: dict[str, str] = {
    "Bash": "shell_exec",
    "Write": "file_write",
    "Edit": "file_write",
    "NotebookEdit": "file_write",
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
        if bare in ALWAYS_ALLOWED_TOOLS:
            return _allow()
        if bare in TOOL_CATEGORY_MAP:
            return _decide_for(TOOL_CATEGORY_MAP[bare], label=tool_name)

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
