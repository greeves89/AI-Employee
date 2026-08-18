"""Was die Sprachfront können muss — und woher sie es bekommt.

**Vorgabe des Nutzers (18.08.2026): „Sprachinteraktion IMMER über MCP-Services."**

Anlass: die Sprachfront pflegte eine EIGENE, handgeschriebene Werkzeugliste.
Gemessen am selben Tag: **42 Werkzeuge gegen 79 beim Agenten**, teils mit
anderen Namen für dasselbe (``search_brain`` vs. ``brain_search``,
``save_memory`` vs. ``memory_save``). Zwei Listen, die niemand gegeneinander
hielt — dasselbe Muster, das am selben Tag einen Codex-Agenten entwaffnete
(doppelter ``msgraph``-Abschnitt) und die Freigabe-Anzeige dreimal existieren
ließ.

**Warum hier nicht einfach „alle 79" steht.** Ein Teil der Werkzeuge lebt im
Agenten-Container und kann vom Orchestrator gar nicht ausgeführt werden:
``bash``, ``write_file``, ``glob``, ``git_diff`` brauchen dessen Dateisystem.
Die gehören DELEGIERT (``ask_agent``), nicht nachgebaut — der Agent hat sie
bereits. Sie in der Sprachfront zu spiegeln hieße, eine zweite Ausführung
danebenzustellen; genau das soll die Vorgabe verhindern.

Deshalb wird jedes Werkzeug des Agenten hier EINMAL eingeordnet:

``DIREKT``     Der Orchestrator ist ohnehin die Gegenstelle (Gedächtnis, Wissen,
              Aufgaben, Zeitpläne, Benachrichtigungen). Die Sprachfront ruft es
              über den MCP-Endpunkt der Plattform auf — dieselbe Gegenstelle,
              die der Agent benutzt.
``DELEGIERT`` Läuft im Agenten-Container. Die Sprachfront gibt die Arbeit per
              ``ask_agent`` weiter.

Der Test dazu (``test_voice_tool_parity.py``) lässt kein Werkzeug uneingeordnet.
Kommt eines dazu, muss jemand entscheiden, wohin es gehört — statt dass es in
der Sprachfront stillschweigend fehlt.
"""

#: Werkzeuge, deren Gegenstelle der Orchestrator ist. Die Sprachfront muss sie
#: erreichen können — direkt über MCP, nicht über einen Umweg.
DIREKT = frozenset({
    # Gedächtnis
    "memory_save", "memory_search", "memory_list", "memory_delete",
    # Gemeinsames Wissen
    "brain_search", "brain_get", "brain_list", "brain_related",
    "brain_contribute", "brain_update", "brain_delete",
    "secondbrain_search", "secondbrain_read", "secondbrain_write", "secondbrain_list",
    # Aufgaben und Zusammenarbeit
    "create_task", "create_task_batch", "delegate_and_wait", "list_tasks",
    "get_tasks_status", "rate_task", "send_message", "send_message_and_wait",
    "list_team", "list_my_team", "list_team_tasks", "list_agent_messages",
    "get_agent_conversation", "schedule_meeting",
    # Planung
    "plan_day", "get_day_plan", "create_schedule", "list_schedules", "manage_schedule",
    "list_todos", "update_todos", "complete_todo",
    "trigger_create", "trigger_delete", "trigger_list", "trigger_toggle",
    # Den Nutzer erreichen
    "notify_user", "send_telegram", "send_voice",
    "request_approval", "check_approval", "escalate_if_unsure",
    "present_file", "present_image", "present_view",
    # Fähigkeiten und Fremdsysteme
    "skill_get_my_skills", "skill_install", "skill_propose", "skill_rate",
    "skill_update", "create_skill", "tickets",
})

#: Werkzeuge, die im Agenten-Container laufen. Die Sprachfront delegiert sie.
#: Sie hier zu spiegeln hieße, eine zweite Ausführung danebenzustellen.
DELEGIERT = frozenset({
    "bash", "write_file", "edit_file", "multi_edit", "read_file", "list_files",
    "glob", "grep", "git_status", "git_diff",
    "web_search", "web_fetch", "view_image",
    "browser", "computer_use",
    "install_package", "skill_search",
    "list_apps", "start_app", "stop_app", "rebuild_app", "app_logs",
    "complete_onboarding",
})

#: Andere Namen für dieselbe Sache. Die Sprachfront ist historisch gewachsen und
#: nennt manches anders; solange das so ist, zählt der Eintrag als vorhanden.
#: Beim Umzug auf MCP verschwinden diese Doppelnamen — dann ist die Tabelle leer.
ANDERS_BENANNT = {
    "brain_search": "search_brain",
    "brain_get": "read_brain",
    "brain_contribute": "write_brain",
    "brain_related": "brain_connections",
    "memory_save": "save_memory",
    "memory_search": "search_knowledge",
    "plan_day": "plan_my_day",
    "list_tasks": "list_agent_tasks",
    "get_tasks_status": "get_delegated_tasks",
    "create_task": "plan_task",
    "delegate_and_wait": "ask_agent",
    "create_task_batch": "delegate_tasks",
    "manage_schedule": "manage_schedules",
    "list_schedules": "manage_schedules",
    "list_files": "list_workspace",
    "read_file": "open_file",
    "grep": "search_files",
    "glob": "search_files",
    "computer_use": "desktop",
    "present_image": "show_on_screen",
    "present_file": "show_on_screen",
}

#: Was die Sprachfront HEUTE noch nicht erreicht. Diese Liste ist eine Schuld,
#: keine Erlaubnis: sie darf nur schrumpfen. Der Test hält sie fest, damit die
#: Lücke sichtbar bleibt, statt in einer stillen Differenz zweier Listen zu
#: verschwinden — so ist sie überhaupt erst aufgefallen.
#:
#: Die schwerwiegendste Zeile ist ``escalate_if_unsure``: ohne sie RÄT die
#: Sprachfront, statt bei Unsicherheit an einen Menschen abzugeben. Genau dafür
#: wurde das Konfidenz-Gate gebaut.
NOCH_OFFEN = frozenset({
    "create_schedule",
    "notify_user", "send_telegram", "send_voice",
    "memory_list", "memory_delete",
    "brain_list", "brain_update", "brain_delete",
    "secondbrain_list", "secondbrain_read", "secondbrain_write", "secondbrain_search",
    "list_team", "list_my_team", "list_team_tasks", "list_agent_messages",
    "get_agent_conversation", "send_message", "send_message_and_wait",
    "schedule_meeting", "rate_task",
    "update_todos", "complete_todo",
    "trigger_create", "trigger_delete", "trigger_list", "trigger_toggle",
    "skill_get_my_skills", "skill_install", "skill_propose", "skill_rate",
    "skill_update", "create_skill", "tickets",
    "request_approval", "check_approval", "present_view",
})


def einordnung(werkzeug: str) -> str:
    """``direkt``, ``delegiert`` — oder ``unbekannt``, was ein Fehler ist."""
    if werkzeug in DIREKT:
        return "direkt"
    if werkzeug in DELEGIERT:
        return "delegiert"
    return "unbekannt"


def sprachname(werkzeug: str) -> str:
    """Wie das Werkzeug in der Sprachfront heißt (heute)."""
    return ANDERS_BENANNT.get(werkzeug, werkzeug)
