"""Weiterreich-Modus der Echtzeit-Sprachfront: Nova ist nur Ohr und Mund.

Warum es das gibt
-----------------
Im Echtzeit-Gespraech (Nova Sonic / Azure Realtime) antwortet nicht der Agent,
sondern das Echtzeit-Modell selbst — mit eigener Werkzeugliste und eigenem,
deutlich kleinerem Verstand. Fuer "wie spaet ist es" ist das richtig schnell und
gut. Fuer ein Gespraech ueber den eigenen Trainingsplan ist es falsch: der Agent
im Container hat das bessere Modell, sein Brain, seine Skills und seine
MCP-Rechte — die Sprachfront nicht. Gemeldet am 23.09.2026: der Triathlon-Coach
sagte am Telefon, er habe keinen Garmin-Zugriff, waehrend derselbe Agent im
Textchat die Schlafdaten sofort lieferte.

Bisher gab es nur zwei Wege: Echtzeit (schnell, aber eben nicht der Agent) oder
die klassische Kette (der Agent, aber Push-to-Talk). Dieser Modus verbindet
beides: Nova bleibt fuer das freihaendige Gespraech zustaendig — Zuhoeren,
Sprechen, Reinreden —, reicht aber JEDE inhaltliche Bitte per ``ask_agent`` an
den Agenten weiter und liest dessen Antwort vor.

Steuerung
---------
- Plattform-Vorgabe: Einstellung ``voice_delegate_to_agent`` ("true"/"false").
- Pro Agent: ``config["voice_delegate_to_agent"]`` (true/false) — gewinnt immer.
  Fehlt der Eintrag (oder ist er None), gilt die Plattform-Vorgabe.
"""

from __future__ import annotations

import json

#: Schluessel im Plattform-Einstellungsspeicher und in ``agent.config``.
SETTING_KEY = "voice_delegate_to_agent"
CONFIG_KEY = "voice_delegate_to_agent"

#: Werkzeuge, die im Weiterreich-Modus bleiben. Alles andere — Kalender, Mail,
#: Brain, MCP-Dienste — soll der Agent selbst tun, nicht die Sprachfront. Was
#: hier bleibt, dient nur der Gespraechsfuehrung: weiterreichen, nachschaerfen,
#: abbrechen, nachfragen was laeuft, Hilfe, Anzeige.
KEEP_TOOLS = frozenset({
    "ask_agent",
    "delegate_tasks",
    "refine_task",
    "cancel_task",
    "get_delegated_tasks",
    "voice_help",
    "rename_conversation",
    "show_on_screen",
    "control_ui",
})

#: ``ask_agent`` in einer Fassung, die das Gegenteil der Vorgabe sagt: nicht
#: "nur fuer echte Arbeit", sondern "fuer alles Inhaltliche". Die normale
#: Beschreibung verbietet ausdruecklich Status- und Wissensfragen — damit wuerde
#: das Modell hier genau das Falsche lernen.
ASK_AGENT_DELEGATE_TOOL = {
    "toolSpec": {
        "name": "ask_agent",
        "description": (
            "Reicht die Frage oder Bitte des Nutzers an den eigentlichen Agenten weiter, "
            "der sie mit seinem Modell, seinem Wissen und seinen angebundenen Diensten "
            "beantwortet. IMMER benutzen, sobald der Nutzer etwas wissen will oder etwas "
            "getan haben moechte — auch bei einfachen Fragen, Daten, Plaenen, Einschaetzungen "
            "und Rueckfragen zu einer vorigen Antwort. Du bekommst sofort eine kurze "
            "Bestaetigung zum Aussprechen; die eigentliche Antwort kommt von selbst nach "
            "einigen Sekunden und wird dann vorgelesen."
        ),
        "inputSchema": {"json": json.dumps({
            "type": "object",
            "properties": {
                "instruction": {
                    "type": "string",
                    "description": (
                        "Die Frage oder Bitte des Nutzers, moeglichst in seinem Wortlaut, "
                        "ergaenzt um den Gespraechszusammenhang, den der Agent braucht."
                    ),
                }
            },
            "required": ["instruction"],
        })},
    }
}


def _as_bool(value) -> bool | None:
    """True/False aus Einstellungs- und Config-Werten; None = nicht gesetzt."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "ja", "on"):
        return True
    if text in ("false", "0", "no", "nein", "off"):
        return False
    return None  # "" oder Unsinn: wie nicht gesetzt


def resolve(agent_config: dict | None, platform_value) -> bool:
    """Ist der Weiterreich-Modus fuer diesen Agenten an? Agent-Wert gewinnt."""
    own = _as_bool((agent_config or {}).get(CONFIG_KEY))
    if own is not None:
        return own
    return bool(_as_bool(platform_value))


def filter_tools(tools: list[dict]) -> list[dict]:
    """Werkzeugliste der Sprachfront auf das Weiterreichen zuschneiden.

    ``ask_agent`` wird durch die Weiterreich-Fassung ersetzt, alles ausserhalb von
    ``KEEP_TOOLS`` faellt weg. Die Reihenfolge bleibt erhalten.
    """
    result: list[dict] = []
    for tool in tools:
        name = str(((tool or {}).get("toolSpec") or {}).get("name") or "")
        if name == "ask_agent":
            result.append(ASK_AGENT_DELEGATE_TOOL)
        elif name in KEEP_TOOLS:
            result.append(tool)
    if not any(
        ((t.get("toolSpec") or {}).get("name") == "ask_agent") for t in result
    ):
        result.insert(0, ASK_AGENT_DELEGATE_TOOL)
    return result


def system_prompt(agent_name: str, agent_role: str, language: str) -> str:
    """Systemprompt der Sprachfront im Weiterreich-Modus.

    Bewusst NICHT der normale Prompt plus ein Zusatz: der normale enthaelt eine
    lange Werkzeugwahl ("Kalender → m365_calendar_today (sofort)" usw.) fuer
    Werkzeuge, die es hier nicht gibt, und ausdrueckliche Anweisungen, Dinge
    NICHT weiterzureichen. Ein angehaengter Absatz gegen diese Liste verliert.
    """
    lang = "Deutsch" if (language or "de").startswith("de") else language
    role = f" Deine Rolle: {agent_role}." if agent_role else ""
    return (
        f"Du bist „{agent_name}“, der KI-Agent, mit dem der Nutzer gerade spricht.{role} "
        f"Du sprichst {lang}, natuerlich und knapp, in der ICH-Form.\n"
        "DU WIRST VORGELESEN, NICHT GELESEN: reiner Fliesstext, keine Sternchen, keine "
        "Listen, keine Ueberschriften, keine Zeilenumbrueche — das wird laut mitgesprochen.\n\n"
        "SO ARBEITEST DU IN DIESEM GESPRAECH:\n"
        "Du beantwortest NICHTS aus eigenem Wissen. Sobald der Nutzer etwas wissen will "
        "oder etwas getan haben moechte, rufst du SOFORT ask_agent auf — mit seiner Frage "
        "im Wortlaut und dem noetigen Zusammenhang aus dem Gespraech. Das gilt auch fuer "
        "einfache Fragen, Zahlen, Termine, Daten und fuer Rueckfragen zu einer vorigen "
        "Antwort. Ohne Werkzeug antwortest du nur auf Begruessung, Verabschiedung, Dank "
        "und wenn du akustisch nicht verstanden hast, was gemeint war.\n"
        "Nach dem Aufruf sagst du in einem kurzen Satz, dass du nachschaust. Kommt die "
        "Antwort, gibst du sie VOLLSTAENDIG und inhaltlich UNVERAENDERT wieder: keine "
        "Zahl weglassen, nichts dazuerfinden, keine eigene Einschaetzung ergaenzen. Du "
        "darfst nur die Form fuers Vorlesen glaetten (Aufzaehlungen zu Saetzen, Markup "
        "weglassen). Sprich dabei nie von ‚dem Agenten‘ oder vom ‚Weiterreichen‘ — fuer "
        "den Nutzer bist du es selbst.\n"
        "Solange eine Antwort noch aussteht, darf der Nutzer weiterreden. Aendert er "
        "seine Bitte, nutze refine_task; will er sie nicht mehr, cancel_task.\n"
    )
