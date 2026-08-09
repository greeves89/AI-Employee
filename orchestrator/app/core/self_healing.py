"""Selbstheilung: ein gescheiterter Auftrag stirbt nicht mehr still (#390).

Bisher endete ein Fehlschlag bei „Task failed" plus einer Benachrichtigung. Für
unbeaufsichtigten Betrieb ist das zu wenig: die halben Fehlschläge sind ein
Zeitablauf, eine überlastete Schnittstelle oder ein 503 — beim zweiten Versuch wäre
nichts davon passiert. Und wer nachts arbeitet, will nicht wegen eines
Verbindungsabbruchs geweckt werden.

**Drei Fragen, in dieser Reihenfolge:**

1. *Ist der Fehler vorübergehend?* Ein Zeitablauf ja, ein falsches Kennwort nie.
   Einen dauerhaften Fehler zu wiederholen kostet Geld und ändert nichts.
2. *Wenn ja: dasselbe nochmal, oder anders?* Erst dasselbe mit Wartezeit. Hilft das
   zweimal nicht, war es offenbar doch nicht nur die Leitung — dann wird die
   Vorgehensweise geändert (kleinere Schritte, anderes Modell).
3. *Wenn nichts hilft: den Menschen holen* — mit dem gesammelten Verlauf, nicht mit
   einem nackten „Task failed".

Bewusst **ohne Sprachmodell**: die Einordnung ist Textvergleich. Ein Modell zu
fragen, warum ein Modellaufruf gescheitert ist, ist genau dann unzuverlässig, wenn
man es braucht — nämlich wenn der Anbieter gerade nicht antwortet.
"""

import re

# ── Einordnung ───────────────────────────────────────────────────────────────

TRANSIENT = "transient"
PERMANENT = "permanent"
UNKNOWN = "unknown"

# Vorübergehend: die Sache selbst ist in Ordnung, der Weg dorthin war es kurz nicht.
_TRANSIENT_PATTERNS = (
    r"\btimed?[ -]?out\b", r"\btimeout\b", r"zeitüberschreitung",
    r"\brate[ -]?limit", r"\btoo many requests\b", r"\b429\b",
    r"\b50[0234]\b", r"\bbad gateway\b", r"\bservice unavailable\b",
    r"\bgateway timeout\b", r"\binternal server error\b",
    r"\boverloaded\b", r"\bcapacity\b", r"\btemporarily unavailable\b",
    r"\btry again\b", r"\bconnection reset\b", r"\bconnection refused\b",
    r"\bconnection (aborted|error)\b", r"\bbroken pipe\b", r"\beof occurred\b",
    r"\bnetwork\b.*\b(error|unreachable)\b", r"\bdns\b", r"\btemporary failure\b",
    r"\bremote end closed\b", r"\bread timeout\b", r"\bssl\b.*\bhandshake\b",
    r"\bdeadlock\b", r"\block wait timeout\b",
)

# Dauerhaft: ein zweiter Versuch bringt dasselbe Ergebnis. Wiederholen wäre nur
# teurer, nicht besser — hier wird sofort eskaliert.
_PERMANENT_PATTERNS = (
    r"\b401\b", r"\b403\b", r"\bunauthorized\b", r"\bforbidden\b",
    r"\bauthentication\b", r"\binvalid api[- ]?key\b", r"\binvalid token\b",
    r"\bpermission denied\b", r"\bnot permitted\b", r"\bkeine berechtigung\b",
    r"\bcredit balance\b", r"\binsufficient (funds|quota|credits)\b",
    r"\bbudget\b.*\b(exceeded|exhausted|erschöpft)\b",
    r"\b404\b", r"\bnot found\b", r"\bno such file\b",
    r"\bsyntax ?error\b", r"\bmodulenotfounderror\b", r"\bimporterror\b",
    r"\bnameerror\b", r"\btypeerror\b", r"\bassertionerror\b",
    r"\bmodel .* (not supported|does not exist)\b",
    r"\bnicht unterstützt\b", r"\bungültig",
    r"\bvalidation ?error\b", r"\binvalid request\b", r"\b400\b",
)

_TRANSIENT_RE = re.compile("|".join(_TRANSIENT_PATTERNS), re.IGNORECASE)
_PERMANENT_RE = re.compile("|".join(_PERMANENT_PATTERNS), re.IGNORECASE)


def classify_error(error: str | None) -> str:
    """Vorübergehend, dauerhaft oder unklar?

    Dauerhaft schlägt vorübergehend, wenn beides passt: „503 — invalid api key"
    liest sich vorne wie ein Ausfall, ist aber keiner. Ein Wiederholen würde den
    echten Grund nur verschleppen.
    """
    text = (error or "").strip()
    if not text:
        # Kein Text heisst nicht „harmlos": ein Lauf, der ohne Meldung endet, ist
        # oft abgebrochen worden (Speicher, Absturz) — das ist wiederholbar.
        return UNKNOWN
    if _PERMANENT_RE.search(text):
        return PERMANENT
    if _TRANSIENT_RE.search(text):
        return TRANSIENT
    return UNKNOWN


# ── Regelwerk ────────────────────────────────────────────────────────────────

# Reihenfolge der Vorgehensweisen. Erst billig und gleich, dann anders.
STRATEGY_RETRY = "retry"
STRATEGY_DECOMPOSE = "decompose"
STRATEGY_OTHER_MODEL = "other_model"

DEFAULT_POLICY = {
    "enabled": True,
    # Wie oft insgesamt neu versucht wird — ueber alle Vorgehensweisen zusammen.
    "max_attempts": 3,
    # Wartezeit vor dem ersten neuen Versuch; verdoppelt sich danach.
    "base_delay_seconds": 60,
    "max_delay_seconds": 900,
    # Unklare Fehler mitnehmen? Ja — ein Lauf, der ohne Meldung endet, ist meistens
    # abgebrochen worden, nicht fachlich gescheitert.
    "retry_unknown": True,
}

_KEY = "self_healing"


def policy_for(agent_config: dict | None) -> dict:
    """Regelwerk eines Agenten, mit den Vorgaben aufgefüllt.

    Ein Agent kann einzelne Werte überschreiben; was er nicht setzt, bleibt Vorgabe.
    So wirkt eine spätere Änderung an den Vorgaben auch bei Agenten, die schon
    etwas anderes eingestellt haben.
    """
    raw = ((agent_config or {}).get(_KEY) or {})
    policy = dict(DEFAULT_POLICY)
    for key, value in raw.items():
        if key in policy and value is not None:
            policy[key] = value
    # Gegen Zahlendreher in der Konfiguration: eine 0 wuerde jeden neuen Versuch
    # verhindern, eine 99 den Agenten stundenlang gegen eine Wand laufen lassen.
    # Die Schalter hart auf Wahrheitswerte ziehen: aus der Oberflaeche oder einem
    # Aufruf kann "false" als Text kommen, und ein nichtleerer Text ist wahr —
    # „aus" haette dann eingeschaltet.
    for flag in ("enabled", "retry_unknown"):
        value = policy[flag]
        if isinstance(value, str):
            policy[flag] = value.strip().lower() not in ("", "0", "false", "no", "nein", "off")
        else:
            policy[flag] = bool(value)
    policy["max_attempts"] = max(0, min(int(policy["max_attempts"]), 5))
    policy["base_delay_seconds"] = max(5, min(int(policy["base_delay_seconds"]), 3600))
    policy["max_delay_seconds"] = max(
        policy["base_delay_seconds"], min(int(policy["max_delay_seconds"]), 6 * 3600)
    )
    return policy


def strategy_for_attempt(attempt: int) -> str:
    """Welche Vorgehensweise beim wievielten Versuch.

    Der erste neue Versuch ändert **nichts** — bei einem Ausfall der Gegenstelle
    wäre jede Änderung am Auftrag nur Rauschen. Erst wenn dasselbe zweimal
    scheitert, war es offenbar nicht die Leitung.
    """
    if attempt <= 1:
        return STRATEGY_RETRY
    if attempt == 2:
        return STRATEGY_DECOMPOSE
    return STRATEGY_OTHER_MODEL


def delay_for_attempt(attempt: int, policy: dict) -> int:
    """Wartezeit vor dem Versuch — verdoppelnd, gedeckelt.

    Sofort neu zu versuchen trifft dieselbe überlastete Gegenstelle im selben
    Zustand. Die Wartezeit ist der eigentliche Wirkstoff, nicht die Wiederholung.
    """
    delay = policy["base_delay_seconds"] * (2 ** max(0, attempt - 1))
    return int(min(delay, policy["max_delay_seconds"]))


_DECOMPOSE_HINT = (
    "\n\n---\nHINWEIS ZUR WIEDERHOLUNG: Dieser Auftrag ist bereits gescheitert.\n"
    "Fehler beim letzten Versuch:\n{error}\n\n"
    "Gehe diesmal ANDERS vor: zerlege die Aufgabe in kleinere, einzeln "
    "überprüfbare Schritte und sichere jeden Schritt ab, bevor du den nächsten "
    "beginnst. Wiederhole nicht denselben Weg."
)

_MODEL_HINT = (
    "\n\n---\nHINWEIS ZUR WIEDERHOLUNG: Dieser Auftrag ist mehrfach gescheitert.\n"
    "Fehler beim letzten Versuch:\n{error}\n\n"
    "Letzter Anlauf mit einem anderen Modell. Wenn der Auftrag so nicht lösbar "
    "ist, sage das ausdrücklich, statt einen Teilerfolg als Erfolg auszugeben."
)


def build_retry_prompt(original_prompt: str, strategy: str, error: str | None) -> str:
    """Der Auftragstext für den nächsten Versuch.

    Bei reinem Wiederholen bleibt er **unverändert** — sonst bekäme der Agent bei
    einem Netzausfall eine Anweisung, die mit der Ursache nichts zu tun hat.
    """
    excerpt = " ".join((error or "").split())[:600] or "(keine Meldung)"
    if strategy == STRATEGY_DECOMPOSE:
        return original_prompt + _DECOMPOSE_HINT.format(error=excerpt)
    if strategy == STRATEGY_OTHER_MODEL:
        return original_prompt + _MODEL_HINT.format(error=excerpt)
    return original_prompt


def plan_next_attempt(
    *,
    error: str | None,
    attempt_so_far: int,
    policy: dict,
) -> dict | None:
    """Der nächste Versuch — oder ``None``, wenn der Mensch dran ist.

    ``None`` ist hier **kein Fehlerfall**, sondern eine von zwei gültigen
    Antworten: entweder es wird nochmal versucht, oder es wird eskaliert. Wer das
    verwechselt, baut eine Selbstheilung, die bei dauerhaften Fehlern schweigt.
    """
    if not policy.get("enabled", True):
        return None

    kind = classify_error(error)
    if kind == PERMANENT:
        return None
    if kind == UNKNOWN and not policy.get("retry_unknown", True):
        return None

    attempt = attempt_so_far + 1
    if attempt > policy["max_attempts"]:
        return None

    strategy = strategy_for_attempt(attempt)
    return {
        "attempt": attempt,
        "strategy": strategy,
        "classification": kind,
        "delay_seconds": delay_for_attempt(attempt, policy),
        "is_last": attempt >= policy["max_attempts"],
    }


def escalation_summary(history: list[dict], error: str | None) -> str:
    """Was der Mensch zu sehen bekommt, wenn nichts mehr hilft.

    Der Verlauf gehört dazu — „drei Versuche, zweimal Zeitablauf, einmal 500"
    beantwortet die erste Rückfrage schon, bevor sie gestellt wird.
    """
    lines = []
    for entry in history:
        lines.append(
            f"  Versuch {entry.get('attempt')} ({entry.get('strategy')}): "
            f"{entry.get('classification')} — "
            f"{' '.join(str(entry.get('error') or '').split())[:160] or '(keine Meldung)'}"
        )
    last = " ".join((error or "").split())[:400] or "(keine Meldung)"
    body = "Selbstheilung erschöpft.\n"
    if lines:
        body += "Bisherige Versuche:\n" + "\n".join(lines) + "\n"
    body += f"Letzter Fehler: {last}"
    return body
