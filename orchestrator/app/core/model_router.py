"""Content-aware model routing — auto-pick a model per task like OpenWebUI's router.

Opt-in per agent (``agent.config["model_router"]``). Deliberately NOT another
LLM call: classification is a fast, free, deterministic heuristic over the
prompt text, so routing itself never adds latency or cost. Combines with the
existing budget-downgrade policy in task_router.py — that check still runs
AFTER this one and can downgrade further, giving "content AND cost" exactly
as intended: content picks the ideal tier, budget can still force the cheap
one regardless of what content wanted.
"""

from typing import Literal

Complexity = Literal["simple", "standard", "complex"]

# Keyword signals per tier. Matched case-insensitively as substrings against
# the prompt. Order matters: complex is checked first (a prompt can contain
# both a greeting and a debugging request — treat it as complex).
_COMPLEX_SIGNALS = (
    "debug", "fehler", "bug", "warum funktioniert", "analysiere", "vergleiche",
    "architektur", "optimiere", "refactor", "sicherheitslücke", "beweise",
    "warum ist", "erkläre im detail", "schritt für schritt",
)
_CODE_SIGNALS = (
    "```", "function ", "def ", "class ", "import ", "traceback", "exception",
    "stacktrace", "kompiliere", "syntax error", "typeerror",
)
_SIMPLE_SIGNALS = (
    "hallo", "hi ", "danke", "ok", "tschüss", "guten morgen", "guten tag",
)

# A prompt at or below this length with none of the above signals is treated
# as simple (short chit-chat / confirmations rarely need a strong model).
_SIMPLE_MAX_LEN = 40


def classify_prompt(prompt: str) -> Complexity:
    """Classify a prompt into a complexity tier using cheap heuristics."""
    text = (prompt or "").strip().lower()
    if not text:
        return "simple"
    if any(sig in text for sig in _COMPLEX_SIGNALS):
        return "complex"
    if any(sig in text for sig in _CODE_SIGNALS):
        return "complex"
    if len(text) <= _SIMPLE_MAX_LEN and any(sig in text for sig in _SIMPLE_SIGNALS):
        return "simple"
    if len(text) <= _SIMPLE_MAX_LEN:
        return "simple"
    return "standard"


DEFAULT_ROUTER_RULES: dict[Complexity, str] = {
    "simple": "claude-haiku-4-5-20251001",
    "standard": "claude-sonnet-5",
    "complex": "claude-opus-5",
}


def route_model(prompt: str, rules: dict[str, str] | None = None) -> str | None:
    """Return the model the router picks for this prompt, or None if the
    resolved tier has no rule configured (caller keeps its own default)."""
    tier = classify_prompt(prompt)
    # Leere Regeln zaehlen als NICHT konfiguriert, nicht als „Modell namens ''".
    #
    # Die Oberflaeche zeigte die Vorgaben bis 1.253.x nur als Platzhalter; wer
    # den Router einschaltete, speicherte drei leere Strings. ``dict.get`` gab
    # die brav zurueck, und der Aufrufer schrieb sie als Modellnamen in den
    # Auftrag — auf der Anlage 146 Auftraege mit leerem Modell. Aufgefallen ist
    # es nie, weil der Agent am Ende auf seine Vorgabe zurueckfiel: der Router
    # war eingeschaltet und wirkungslos.
    bereinigt = {k: v for k, v in (rules or {}).items() if (v or "").strip()}
    table = {**DEFAULT_ROUTER_RULES, **bereinigt}
    gewaehlt = (table.get(tier) or "").strip()
    return gewaehlt or None
