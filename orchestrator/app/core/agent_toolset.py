"""Was ein Agent tatsächlich kann — je nach Laufzeit.

Für die Befehlsliste im Chat („/"). Der Gedanke dahinter ist wichtiger als der
Code: es wird **nichts Einheitliches erfunden**. Ein Claude-Code-Agent bekommt das
zu sehen, was Claude Code hat; ein Codex-Agent das von Codex; ein Custom-LLM-Agent
seinen Werkzeugsatz plus die installierten Skills. Eine erfundene gemeinsame Liste
wäre bei jeder Laufzeit ein bisschen falsch.

**Warum ein Katalog und keine Abfrage beim Agenten.** Der Orchestrator sieht das
Verzeichnis ``agent/`` nicht (es ist nicht in seinen Container gemountet), und den
Agenten zu fragen ginge nur, solange er läuft — die Liste soll aber auch bei einem
ruhenden Agenten stehen. Gegen das Auseinanderlaufen steht ein Test, der die echten
Werkzeugdefinitionen im Repo gegen diesen Katalog hält: ``test_agent_toolset.py``.
Dasselbe Muster wie bei der Harness-Parität.

Dynamisch bleibt, was pro Agent verschieden ist: eingebundene MCP-Server und
installierte Skills. Die kommen aus der Datenbank, nicht von hier.
"""

# ── Claude Code ──────────────────────────────────────────────────────────────

# Was Claude Code von sich aus mitbringt. Wir richten es nicht ein und können es
# nicht abschalten — außer über die Autonomie-Matrix, die im Prompt landet.
CLAUDE_CODE_BUILTINS = [
    "Read", "Write", "Edit", "Bash", "Glob", "Grep",
    "WebFetch", "WebSearch", "TodoWrite", "Task", "NotebookEdit",
]

# Claude Codes eigene Befehle. Sie laufen INNERHALB der CLI und nicht über uns —
# im kopflosen Betrieb (``claude -p``) sind sie nicht erreichbar. Sie stehen hier,
# damit die Liste ehrlich ist: der Agent hat sie, die Oberfläche kann sie nicht
# auslösen.
CLAUDE_CODE_OWN_COMMANDS = [
    ("compact", "Verlauf verdichten (läuft in der CLI, nicht von hier)"),
    ("clear", "Verlauf verwerfen (läuft in der CLI)"),
    ("cost", "Kosten des Laufs (läuft in der CLI)"),
]

# Die MCP-Server, die JEDER Claude-Code-Agent beim Start registriert bekommt
# (agent/app/main.py). Custom-MCP-Server kommen pro Agent aus der Datenbank dazu.
MCP_SERVER_TOOLS: dict[str, list[str]] = {
    "brain": [
        "brain_contribute", "brain_delete", "brain_get", "brain_list",
        "brain_related", "brain_search", "brain_update"
    ],
    "computer-use": [
        "computer_ax_tree", "computer_click", "computer_close_app", "computer_drag",
        "computer_find_element", "computer_get_clipboard", "computer_key",
        "computer_list_sessions", "computer_move", "computer_open_app",
        "computer_screenshot", "computer_scroll", "computer_set_clipboard",
        "computer_type", "computer_use_session", "computer_wait_for_element"
    ],
    "email": [
        "email_list", "email_mark_read", "email_read", "email_reply",
        "email_search", "email_send"
    ],
    "hyperframes": [
        "get_docs", "list_videos", "render_video"
    ],
    "memory": [
        "memory_delete", "memory_list", "memory_save", "memory_search"
    ],
    "msgraph": [
        "complete_todo_task", "create_calendar_event", "create_planner_task",
        "create_todo_task", "delete_calendar_event", "list_calendar_events",
        "list_channels", "list_emails", "list_onedrive_files", "list_planner_plans",
        "list_planner_tasks", "list_teams", "list_teams_chats", "list_todo_lists",
        "list_todo_tasks", "read_email", "read_onedrive_file", "reply_email",
        "search_onedrive", "send_email", "send_teams_chat_message",
        "send_teams_message", "update_calendar_event", "update_planner_task"
    ],
    "notification": [
        "escalate_if_unsure", "notify_user", "present_file", "request_approval",
        "send_telegram"
    ],
    "orchestrator": [
        "app_logs", "complete_onboarding", "complete_todo", "create_schedule",
        "create_skill", "create_task", "create_task_batch", "delegate_and_wait",
        "get_agent_conversation", "get_day_plan", "get_tasks_status",
        "list_agent_messages", "list_apps", "list_my_team", "list_schedules",
        "list_tasks", "list_team", "list_team_tasks", "list_todos",
        "manage_schedule", "plan_day", "rate_task", "rebuild_app",
        "schedule_meeting", "send_message", "send_message_and_wait", "skill_update",
        "start_app", "stop_app", "tickets", "trigger_create", "trigger_delete",
        "trigger_list", "trigger_toggle", "update_todos"
    ],
    "read-logs": [
        "read_logs"
    ],
    "skill": [
        "skill_get_my_skills", "skill_install", "skill_propose", "skill_rate",
        "skill_record_usage", "skill_search", "skill_update"
    ],
}

DEFINITION_TOOLS = [
    "app_logs",
    "bash",
    "brain_contribute",
    "brain_delete",
    "brain_get",
    "brain_list",
    "brain_related",
    "brain_search",
    "brain_update",
    "browser",
    "check_approval",
    "complete_onboarding",
    "complete_todo",
    "computer_use",
    "create_schedule",
    "create_skill",
    "create_task",
    "create_task_batch",
    "delegate_and_wait",
    "edit_file",
    "escalate_if_unsure",
    "get_agent_conversation",
    "get_day_plan",
    "get_tasks_status",
    "git_diff",
    "git_status",
    "glob",
    "grep",
    "list_agent_messages",
    "list_apps",
    "list_files",
    "list_my_team",
    "list_schedules",
    "list_tasks",
    "list_team",
    "list_team_tasks",
    "list_todos",
    "manage_schedule",
    "memory_delete",
    "memory_list",
    "memory_save",
    "memory_search",
    "multi_edit",
    "notify_user",
    "plan_day",
    "present_file",
    "present_image",
    "rate_task",
    "read_file",
    "rebuild_app",
    "request_approval",
    "schedule_meeting",
    "secondbrain_list",
    "secondbrain_read",
    "secondbrain_search",
    "secondbrain_write",
    "send_message",
    "send_message_and_wait",
    "send_telegram",
    "send_voice",
    "skill_get_my_skills",
    "skill_install",
    "skill_propose",
    "skill_rate",
    "skill_search",
    "skill_update",
    "start_app",
    "stop_app",
    "tickets",
    "trigger_create",
    "trigger_delete",
    "trigger_list",
    "trigger_toggle",
    "update_todos",
    "view_image",
    "web_fetch",
    "web_search",
    "write_file",
]


# ── Befehle der Plattform ────────────────────────────────────────────────────

# Die gelten überall gleich, weil sie NICHT im Agenten laufen, sondern auf dem
# gespeicherten Verlauf. Genau deshalb funktioniert /compact in allen drei
# Laufzeiten identisch, obwohl die Kompaktierung darin es nicht tut.
PLATFORM_COMMANDS = [
    ("compact", "Kontext anzeigen und den Verlauf verdichten"),
    ("planen", "Nur den Weg beschreiben, nichts ausführen"),
    ("zusammenfassen", "In frischem Gespräch weiterreden"),
    ("verzweigen", "Ab der letzten Nachricht abzweigen"),
    ("zurueckspulen", "Auf die letzte Nachricht zurücksetzen"),
    ("tools", "Werkzeuge dieses Agenten zeigen"),
]


def _mode_of(agent) -> str:
    """Die wirksame Laufzeit.

    ``claude_code`` mit Anbieter ``codex`` IST ein Codex-Agent — dieselbe
    Umschreibung wie im Agent-Manager. Stünde sie hier anders, zeigte die Liste
    Werkzeuge, die der Agent gar nicht hat.
    """
    mode = getattr(agent, "mode", None) or "claude_code"
    config = getattr(agent, "config", None) or {}
    if mode == "claude_code" and config.get("model_provider") == "codex":
        return "codex_cli"
    return mode


def toolset_for(agent, *, skills: list[str] | None = None,
                extra_mcp: list[str] | None = None) -> dict:
    """Die Ausstattung dieses Agenten, nach Gruppen.

    ``skills`` und ``extra_mcp`` sind das, was pro Agent verschieden ist — sie
    kommen von aussen, weil sie in der Datenbank stehen und nicht in einem
    Katalog stehen können.
    """
    mode = _mode_of(agent)
    groups: list[dict] = []

    if mode == "claude_code":
        groups.append({
            "key": "builtin",
            "label": "Claude Code",
            "note": "bringt die Laufzeit selbst mit",
            "tools": list(CLAUDE_CODE_BUILTINS),
        })
        for server, tools in sorted(MCP_SERVER_TOOLS.items()):
            groups.append({
                "key": f"mcp:{server}",
                "label": f"MCP · {server}",
                "note": "",
                "tools": list(tools),
            })
    else:
        label = "Codex" if mode == "codex_cli" else "Eigenes Modell"
        groups.append({
            "key": "tools",
            "label": f"{label} · Werkzeuge",
            "note": "derselbe Satz in beiden Laufzeiten (Harness-Parität)",
            "tools": list(DEFINITION_TOOLS),
        })

    if extra_mcp:
        groups.append({
            "key": "mcp:custom",
            "label": "Eigene MCP-Server",
            "note": "für diesen Agenten freigeschaltet",
            "tools": sorted(extra_mcp),
        })

    if skills:
        groups.append({
            "key": "skills",
            "label": "Installierte Skills",
            "note": "",
            "tools": sorted(skills),
        })

    commands = [{"name": n, "hint": h} for n, h in PLATFORM_COMMANDS]
    if mode == "claude_code":
        commands += [
            {"name": n, "hint": h, "runtime_only": True}
            for n, h in CLAUDE_CODE_OWN_COMMANDS
        ]

    return {
        "mode": mode,
        "commands": commands,
        "groups": groups,
        "total": sum(len(g["tools"]) for g in groups),
    }

# ── Kontextfenster ───────────────────────────────────────────────────────────
# Aus agent/app/model_registry.py uebernommen — der Orchestrator sieht das
# Agentenverzeichnis nicht. Auch das haelt ein Test gegen die Quelle.
#
# Laengster Treffer gewinnt, damit "gpt-4o-2024-08-06" bei "gpt-4o" landet und
# nicht bei "gpt-4".
CONTEXT_WINDOWS: dict[str, int] = {
    "gpt-4o-mini": 128_000,
    "gpt-4o": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4": 8_192,
    "gpt-3.5-turbo": 16_385,
    "gpt-5": 1_000_000,
    "o1-mini": 128_000,
    "o1": 200_000,
    "o3-mini": 200_000,
    # Anthropic laut platform.claude.com/docs (geprueft 2026-08-11).
    "claude-fable-5": 1_000_000,
    "claude-mythos-5": 1_000_000,
    "claude-opus-5": 1_000_000,
    "claude-sonnet-5": 1_000_000,
    "claude-opus-4-8": 1_000_000,
    "claude-opus-4-7": 1_000_000,
    "claude-opus-4-6": 1_000_000,
    "claude-opus-4-5": 200_000,
    "claude-opus-4-1": 200_000,
    "claude-opus-4": 200_000,
    "claude-sonnet-4-6": 1_000_000,
    "claude-sonnet-4-5": 200_000,
    "claude-sonnet-4": 200_000,
    "claude-haiku-4-5": 200_000,
    "claude-haiku-4": 200_000,
    "gemini-2.5-pro": 1_000_000,
    "gemini-2.5-flash": 1_000_000,
    "gemini-1.5-pro": 2_000_000,
    "gemini-1.5-flash": 1_000_000,
    "llama": 8_192,
    "mistral": 32_768,
    "codestral": 32_768,
    "deepseek": 128_000,
    "qwen": 128_000,
}

# Unbekannt heisst hier UNBEKANNT, nicht 128k.
#
# Der Anlass ist konkret: auf dem Pi laeuft claude-sonnet-5, und das steht in
# keiner der Tabellen — die Anzeige behauptete daraufhin ein 128k-Fenster. Eine
# erfundene Zahl ist in einer Kontextanzeige schlimmer als ein ehrliches "?": sie
# verspricht Luft, die es vielleicht nicht gibt, oder sie draengt zum Verdichten,
# wo gar kein Grund ist.
#
# Der Agent selbst faellt fuer SEINE Kompaktierungsschwelle weiterhin auf 128k
# zurueck (agent/app/model_registry.py). Das ist dort richtig: zu frueh zu
# verdichten kostet einen Zusammenfassungsaufruf, zu spaet kostet den Lauf.


def context_window_for(model: str | None) -> int | None:
    """Fenstergroesse eines Modells, oder ``None`` wenn wir sie nicht kennen.

    Laengster Treffer gewinnt, damit "gpt-4o-2024-08-06" bei "gpt-4o" landet und
    nicht bei "gpt-4".
    """
    name = (model or "").lower()
    best = ""
    for key in CONTEXT_WINDOWS:
        if key in name and len(key) > len(best):
            best = key
    return CONTEXT_WINDOWS[best] if best else None
