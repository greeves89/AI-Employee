import json
import logging

from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

SHARED_TOKEN_PATH = "/shared/.auth/token.json"


class Settings(BaseSettings):
    agent_id: str = "agent-001"
    agent_name: str = ""
    # Role sentence the orchestrator passes in (AGENT_ROLE). The CLI runtimes get it
    # through AGENT.md/CLAUDE.md; the custom_llm runtime needs it explicitly — see
    # runner_hooks.get_identity_context().
    agent_role: str = ""
    agent_token: str = ""
    redis_url: str = "redis://redis:6379"
    health_port: int = 8080
    default_model: str = "claude-sonnet-4-6"
    max_turns: int = 100
    extended_thinking: bool = False  # Not a CLI flag - thinking is model-controlled
    tool_max_concurrency: int = 10  # Max parallel concurrent-safe tool calls (TOOL_MAX_CONCURRENCY)
    anthropic_api_key: str = ""
    claude_code_oauth_token: str = ""
    openai_api_key: str = ""
    workspace_dir: str = "/workspace"
    orchestrator_url: str = "http://orchestrator:8000"

    # Agent mode: "claude_code" (default CLI) or "custom_llm" (direct API)
    agent_mode: str = "claude_code"

    # Custom LLM settings (only used when agent_mode == "custom_llm")
    llm_provider_type: str = ""  # "openai" | "anthropic" | "google"
    llm_api_endpoint: str = ""
    llm_api_key: str = ""
    llm_model_name: str = ""
    # Obergrenze der Antwortlänge. **0 = keine eigene Grenze** (Vorgabe): dann
    # entscheidet das Modell, und das ist fast immer das Richtige.
    #
    # Bis v1.183.0 standen hier 4096 — eine Zahl aus der Zeit, als Modelle nicht
    # mehr konnten. Für einen Agenten, der ein Review, eine Spezifikation oder eine
    # Datei liefern soll, ist das zu wenig: die Antwort bricht mitten im Satz ab,
    # und das sieht aus wie ein fertiges Ergebnis.
    llm_max_tokens: int = 0
    llm_temperature: float = 0.7
    llm_system_prompt: str = ""
    llm_tools_enabled: bool = True
    llm_thinking_mode: str = "auto"  # "off", "auto", "on"
    llm_reasoning_effort: str = ""  # "" (API default), "low", "medium", "high" — OpenAI reasoning models only
    # Standard-Denktiefe des AGENTEN (Env DEFAULT_REASONING, vom Besitzer in den
    # Agenten-Einstellungen gesetzt): "off", "low", "medium", "high", "max" oder ""
    # (= Auto). Gilt ueberall dort, wo am einzelnen Lauf KEINE Stufe haengt —
    # Aufgaben, Zeitplaene, delegierte Auftraege, Agent-zu-Agent-Nachrichten und
    # Chats ohne gewaehlte Stufe. Eine im Chat gewaehlte Stufe gewinnt immer.
    default_reasoning: str = ""
    llm_api_version: str = ""  # Azure OpenAI api-version (e.g. 2024-10-21)
    # Ausweichmodelle bei Rate-Limit/Zeitueberschreitung/Ueberlastung (#200),
    # kommagetrennt und in dieser Reihenfolge. Leer = kein Ausweichen, dann
    # scheitert der Lauf wie bisher. Nur Bereitstellungsnamen desselben Zugangs —
    # ein anderer Endpunkt braucht einen anderen Zugang.
    llm_fallback_models: str = ""

    # Chat watchdogs. Codex CLI can legitimately spend a long time in a
    # single `codex exec` turn while still streaming tool activity; default 0
    # disables the hard wall-clock timeout for Codex chats.
    chat_turn_timeout_seconds: int = 600
    codex_chat_turn_timeout_seconds: int = 0

    # Custom MCP servers (JSON: {"name": "http://url"}) - used by both modes
    custom_mcp_servers: str = ""

    model_config = {"env_prefix": "", "case_sensitive": False}


settings = Settings()


async def wait_for_new_oauth_token(previous: str, timeout: float = 180.0,
                                   interval: float = 2.0) -> str | None:
    """Warte, bis die Plattform einen ANDEREN Zugangstoken hinterlegt hat.

    Der Anlass steht im Betrieb: Anthropic **rotiert** beim Erneuern — in der
    Sekunde, in der der neue Token ausgestellt wird, ist der alte tot. Faellt das
    in einen laufenden Zug, stirbt der mit „401 access token has been revoked",
    obwohl niemand etwas falsch gemacht hat.

    Bisher wartete der Wiederholversuch pauschal 10 Sekunden. Die Plattform
    schreibt den neuen Token aber erst im naechsten 30-Sekunden-Takt, und wenn sie
    dafuer erst bei Anthropic anfragen muss, dauert es laenger. Der Versuch lief
    also regelmaessig ins Leere und der Nutzer bekam den Fehler rot in den Chat.

    Jetzt wird auf das gewartet, worauf es ankommt: dass sich der Token wirklich
    geaendert hat. Rueckgabe ``None``, wenn er das innerhalb der Frist nicht tut —
    dann darf der Aufrufer es trotzdem versuchen, statt gar nichts zu tun.
    """
    import asyncio as _asyncio

    deadline = _asyncio.get_running_loop().time() + timeout
    while _asyncio.get_running_loop().time() < deadline:
        current = get_oauth_token()
        if current and current != previous:
            logger.info("[Auth] Neuer Zugangstoken erkannt — Zug wird wiederholt")
            return current
        await _asyncio.sleep(interval)
    logger.warning("[Auth] Innerhalb von %.0fs kam kein neuer Token", timeout)
    return None


def get_oauth_token() -> str:
    """Return the most current OAuth token.

    Checks the shared volume JSON file first (written by the orchestrator
    after each token refresh), falling back to the env-var based config.
    """
    try:
        with open(SHARED_TOKEN_PATH) as f:
            data = json.load(f)
        token = data.get("access_token", "")
        if token:
            return token
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return settings.claude_code_oauth_token


# Denk-Budget je Stufe fuer Claude (MAX_THINKING_TOKENS) — Chat UND Aufgaben
# nutzen dieselbe Tabelle. "max" ist bewusst ein Alias fuer "high": 31999 ist
# die Ultrathink-Obergrenze von Claude Code.
CLAUDE_THINKING_BUDGET = {
    "low": "4000", "medium": "10000", "high": "31999",
    # Claude Code hat oberhalb von Ultrathink nichts mehr — beide oberen Stufen
    # laufen deshalb gegen dieselbe Obergrenze.
    "xhigh": "31999", "max": "31999",
}

#: Denkstufen der Oberflaeche, uebersetzt in das, was der jeweilige Harness
#: versteht. EINE Tabelle statt verstreuter dict-Literale: Die Zuordnung stand
#: an drei Stellen (hier, codex_runner, llm_chat_handler), und beim Ergaenzen
#: einer Stufe haette man leicht eine davon uebersehen.
_STUFEN_LLM = {"off": ""}                          # Rest unveraendert
_STUFEN_CODEX = {"off": "minimal", "max": "xhigh"}  # Codex kennt "max" nicht


def reasoning_fuer(harness: str, level: str) -> str:
    """Chat-Stufe in die Schreibweise des Harness uebersetzen.

    ``harness``: "codex" fuer die Codex-Kommandozeile, sonst der LLM-Weg.
    Leere Eingabe bleibt leer — das heisst "nichts erzwingen".
    """
    level = (level or "").strip().lower()
    if not level:
        return ""
    tabelle = _STUFEN_CODEX if harness == "codex" else _STUFEN_LLM
    return tabelle.get(level, level)


def llm_default_reasoning_effort() -> str:
    """Reasoning-Effort fuer den LLM-Provider, wenn am Lauf keine Stufe haengt.

    Die Standard-Denktiefe des Agenten (default_reasoning, Chat-Stufennamen)
    gewinnt vor dem Provider-Feinknopf llm_reasoning_effort — sie ist die
    Einstellung, die der Besitzer sichtbar am Agenten gesetzt hat. Namen werden
    uebersetzt ueber ``reasoning_fuer`` ("off" -> "" = API-Standard ohne Denken).
    """
    level = (settings.default_reasoning or "").strip().lower()
    if level:
        return reasoning_fuer("llm", level)
    return settings.llm_reasoning_effort

