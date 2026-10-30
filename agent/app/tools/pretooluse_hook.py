"""Claude Code PreToolUse hook decision logic (issue #197, Teil 2).

Claude Code agents run their OWN native tool implementations (Bash, Write,
Edit, ...) plus MCP-server tools — none of that passes through
``ToolExecutor.execute()`` (that class only runs for ``mode=custom_llm``).
Containers are launched with ``--dangerously-skip-permissions``, and the
pre-existing ``bash-approval-server.mjs`` MCP tool is only a PARALLEL, opt-in
``Bash`` tool the model can simply not call — the native, unrestricted one
stays available regardless. In practice the whole autonomy matrix / command
policy system was unenforceable for this runtime.

Fix: a PreToolUse hook (fires for EVERY tool call, built-in and MCP alike, is
NOT bypassed by ``--dangerously-skip-permissions``, can deny via
``permissionDecision: "deny"``). Since P0-2 it is a MANAGED command hook
(``/etc/claude-code/managed-settings.json``, root-owned, ``allowManagedHooksOnly``;
source ``agent/richtlinie/``) running ``/opt/mcp/pretooluse-hook.mjs``, which asks
this same container's own health server
(``agent/app/health.py::pretooluse_hook_handler``) and denies when it cannot.

Deliberately reuses ``executor.py``'s ``ALWAYS_ALLOWED_TOOLS``/
``TOOL_CATEGORY_MAP``/``_get_allowed_categories()`` rather than a second,
Claude-Code-specific table — those two runtimes' MCP tool surface is the
SAME set of capabilities (``test_harness_capability_parity.py`` enforces
this), so one classification, applied twice with two small naming
translations (native Claude Code names, and the ``mcp__<server>__<name>``
prefix), stays in sync by construction instead of by habit.

``decide_async()`` (issue #787 Punkt 1) additionally reuses
``executor.py::_evaluate_command_policy`` for the native ``Bash`` tool —
command_policies (regex blocked/high/medium/allow rules) used to only ever
run for ``mode=custom_llm``, never for this runtime's own Bash. Caution:
Claude Code's HTTP PreToolUse hook is fail-OPEN on timeout by documented
design ("A timed-out hook doesn't block the tool call") — a second
sequential network call here makes hitting that timeout more likely on a
cold cache, which is why the hook's configured timeout was raised alongside
this change (now ``agent/richtlinie/managed-settings.json``; the hook script
itself denies after 12 s, before the CLI would cancel it and let the call through).
Separately, ``executor.py::_get_command_policies`` is ALSO fail-open on any
fetch error, not just slowness ("transient orchestrator outages should not
brick agents") — this is a pre-existing, deliberate tradeoff for the
custom_llm runtime, now inherited here by reuse. Accepted knowingly: making
Bash fail-closed during an orchestrator outage would brick every agent's
shell entirely, a worse outcome than the rare window this trades for.
"""
from __future__ import annotations

from app.tools.executor import (
    ALWAYS_ALLOWED_TOOLS,
    TOOL_CATEGORY_MAP,
    _evaluate_command_policy,
    _get_allowed_categories,
)

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
    # Codex CLI (#781 PR 3): eigene Werkzeuge ohne Aussenwirkung — Plan,
    # Bild ansehen, Subagenten, Rueckfrage, MCP-Ressourcen lesen, Werkzeugsuche.
    # Namen aus codex-cli 0.162 (im Programm nachgesehen).
    "update_plan", "view_image", "spawn_agent", "send_input", "wait_agent",
    "close_agent", "resume_agent", "request_user_input", "request_permissions",
    "list_mcp_resources", "read_mcp_resource", "list_mcp_resource_templates",
    "tool_search",
    # Subagenten meldet Codex mit Namensraum davor (live gesehen: 
    # "collaborationspawn_agent"). Der Subagent laeuft selbst unter diesem
    # Hook — live geprueft: sein Shell-Befehl wurde gesperrt.
    "collaborationspawn_agent", "collaborationsend_input", "collaborationwait_agent",
    "collaborationclose_agent", "collaborationresume_agent",
})
_NATIVE_CATEGORY_MAP: dict[str, str] = {
    "Bash": "shell_exec",
    "Monitor": "shell_exec",  # fuehrt wiederholt ein Shell-Kommando aus
    "Write": "file_write",
    "Edit": "file_write",
    "NotebookEdit": "file_write",
    "EnterWorktree": "file_write",  # legt einen git-Worktree im Dateisystem an
    "ExitWorktree": "file_write",
    # Codex CLI: Shell meldet der Hook als "Bash" (oben); die internen Namen
    # stehen zur Sicherheit mit drin, falls eine Version sie durchreicht.
    "apply_patch": "file_write",
    "exec_command": "shell_exec",
    "shell_command": "shell_exec",
    "local_shell": "shell_exec",
    "write_stdin": "shell_exec",
}

# Plattform-eigene MCP-Server, deren Werkzeuge NICHT in den custom_llm-Tabellen
# stehen (dort gibt es sie nicht): `read-logs` (eigene Containerlogs, nur
# lesend) und der Desktop-Server, dessen Werkzeuge `computer_<aktion>` heissen
# und in der Summe genau das sind, was `computer_use` in TOOL_CATEGORY_MAP ist.
_MCP_BARE_ALWAYS_ALLOWED = frozenset({
    "read_logs",
    "skill_record_usage",  # reine Buchfuehrung, wie skill_rate
})
_MCP_BARE_PREFIX_CATEGORY: dict[str, str] = {
    "computer_": TOOL_CATEGORY_MAP["computer_use"],
    # Browser-Steuerung (Playwright bzw. Ego-Browser) — dasselbe Risiko wie das
    # Sammelwerkzeug `browser` der custom_llm-Laufzeit.
    "browser_": TOOL_CATEGORY_MAP["browser"],
    "ego_": TOOL_CATEGORY_MAP["browser"],
}

# Werkzeuge der MCP-Server E-Mail, Microsoft 365 und Videos (#781 PR 2). Bis
# hierhin standen sie in keiner Tabelle: mit konfigurierter Freigabeliste galten
# sie als "unbekannt" und waren gesperrt — auch fuer Claude-Code-Agenten.
# Lesen eigener Daten = Dateien lesen; Schreiben/Senden = die Faehigkeit
# "E-Mail / M365 senden" (Kategorie "custom", wie in autonomy_matrix).
_LESEN = "file_read"
_M365 = "custom"
_MCP_BARE_CATEGORY: dict[str, str] = {
    # email-server
    "email_list": _LESEN, "email_read": _LESEN, "email_search": _LESEN,
    "email_send": _M365, "email_reply": _M365, "email_mark_read": _M365,
    # msgraph-server
    "list_calendar_events": _LESEN, "list_channels": _LESEN, "list_emails": _LESEN,
    "list_onedrive_files": _LESEN, "list_planner_plans": _LESEN,
    "list_planner_tasks": _LESEN, "list_teams": _LESEN, "list_teams_chats": _LESEN,
    "list_todo_lists": _LESEN, "list_todo_tasks": _LESEN, "read_email": _LESEN,
    "read_onedrive_file": _LESEN, "search_onedrive": _LESEN,
    "send_email": _M365, "reply_email": _M365, "send_teams_message": _M365,
    "send_teams_chat_message": _M365, "create_calendar_event": _M365,
    "update_calendar_event": _M365, "delete_calendar_event": _M365,
    "create_planner_task": _M365, "update_planner_task": _M365,
    "create_todo_task": _M365, "complete_todo_task": _M365,
    # hyperframes-server (Videos im eigenen Arbeitsordner)
    "get_docs": _LESEN, "list_videos": _LESEN, "render_video": "file_write",
}


def _decide_for(category: str, *, label: str) -> dict:
    allowed = _get_allowed_categories()
    if allowed is not None and category not in allowed:
        return _deny(
            f"Werkzeug '{label}' braucht Kategorie '{category}', die nicht in der "
            "aktuellen Freigabeliste steht. `request_approval` zuerst aufrufen."
        )
    return _allow()


# Shell-Werkzeuge, deren Befehl zusaetzlich gegen die command_policies laeuft.
_SHELL_WERKZEUGE = frozenset({"Bash", "exec_command", "shell_command", "local_shell", "write_stdin"})

# Schreibschutz (Laufzeit-Konfiguration, Arbeitsbereiche): gemeinsame Regel fuer
# Hook und custom_llm-Executor, siehe app/tools/schreibschutz.py.
from app.tools.schreibschutz import geschuetzte_kategorie  # noqa: E402


def decide(tool_name: str, tool_input: dict | None = None) -> dict:
    """Kategorie-Entscheidung; Schreiben in die Laufzeit-Konfiguration braucht
    zusaetzlich ``system_config`` (siehe ``_LAUFZEIT_KONFIG``)."""
    schutz = geschuetzte_kategorie(tool_name, tool_input)
    if schutz:
        kategorie, grund = schutz
        return _decide_for(kategorie, label=f"{tool_name} ({grund})")
    return _decide_kategorie(tool_name, tool_input)


def _decide_kategorie(tool_name: str, tool_input: dict | None = None) -> dict:
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
        if bare in _MCP_BARE_CATEGORY:
            return _decide_for(_MCP_BARE_CATEGORY[bare], label=tool_name)
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


async def decide_async(tool_name: str, tool_input: dict | None = None, *, cwd: str | None = None,
                       lauf: str | None = None) -> dict:
    """Same category gate as ``decide()``, plus the regex command-policy layer
    for Bash (issue #787 Punkt 1).

    Before this, ``command_policies`` (blocked/high/medium/allow regex rules)
    only ever ran for ``mode=custom_llm`` (``executor.py::_tool_bash``) — a
    Claude Code agent's OWN native ``Bash`` tool bypassed it completely, even
    though the category gate above already treated ``Bash`` the same as any
    other shell_exec call. ``decide()`` itself stays untouched and synchronous
    (60+ existing tests call it directly) — this wraps it instead of changing
    it, and only adds the extra check for ``Bash``.

    Danach die Push-Sperre (#1117, ``push_sperre.pruefen``): kein ``git push`` im
    Arbeitstakt und keiner in den Branch einer fremden Übernahme. ``cwd`` und
    ``lauf`` (der Task) liefert der Aufrufer des Hooks mit; beides darf fehlen.
    """
    base = decide(tool_name, tool_input)
    if base["hookSpecificOutput"]["permissionDecision"] == "deny":
        return base  # category gate already said no — nothing finer to check
    # Läufe aus dem Rückkanal (Frage aus einem Issue) dürfen nur antworten und Karten
    # im Eingang anlegen (push_sperre.werkzeug_pruefen, Entscheidung im Orchestrator).
    from app.tools.push_sperre import werkzeug_pruefen

    lauf_grund = await werkzeug_pruefen(tool_name, tool_input, lauf)
    if lauf_grund:
        return _deny(lauf_grund)
    if tool_name not in _SHELL_WERKZEUGE:
        return base
    eingabe = tool_input or {}
    command = eingabe.get("command") or eingabe.get("cmd") or eingabe.get("chars") or ""
    if isinstance(command, list):
        command = " ".join(str(t) for t in command)
    if not command:
        return base
    effect, reason = await _evaluate_command_policy(command)
    if effect == "blocked":
        return _deny(f"[COMMAND BLOCKED] Policy: {reason or 'Blocked by command policy'}")
    if effect in {"high", "medium"}:
        return _deny(
            f"Befehl entspricht einer Command Policy ({effect}): {reason}. "
            "`request_approval` zuerst aufrufen und auf Freigabe warten, dann "
            "erneut versuchen."
        )
    from app.tools.push_sperre import pruefen as push_pruefen

    grund = await push_pruefen(command, cwd, lauf)
    if grund:
        return _deny(grund)
    return base  # None or "allow" → category decision stands


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
