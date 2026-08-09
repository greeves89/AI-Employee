"""Konfidenz-Routing: bei Unsicherheit den Menschen fragen (#389).

Agenten raten manchmal, statt zu fragen. Ein geratenes Ergebnis ist schlimmer als
eine Rückfrage — es sieht aus wie Arbeit und ist keine.

Die Schwelle liegt **hier**, nicht im Agenten. Das ist der springende Punkt: würde
der Agent selbst entscheiden, ob seine 40 % reichen, wäre die Entscheidung genauso
unsicher wie die Antwort. Er meldet nur eine Zahl; ob die reicht, entscheidet eine
Regel, die der Betreiber gesetzt hat.

**Verzahnt mit dem, was schon da ist:** eskaliert wird über dieselbe Freigabe-Ablage
wie jede andere Rückfrage. Kein zweiter Posteingang, kein zweiter Ort zum Nachsehen.
Und es ergänzt den Probelauf (#386) und die Selbstheilung (#390): drei verschiedene
Anlässe, denselben Menschen zu holen — über denselben Weg.
"""

# Unter 70 von 100 wird gefragt. Bewusst nicht bei 50: „ich bin mir zur Haelfte
# sicher" ist bereits ein Muenzwurf, und den soll niemand ausfuehren.
DEFAULT_THRESHOLD = 70

_KEY = "confidence"


def normalize_confidence(value) -> int:
    """Eine Konfidenz auf 0–100 bringen.

    Modelle liefern mal ``0.4``, mal ``40``, mal ``"85%"``. Alle drei meinen
    dasselbe, und alle drei kommen vor. Ohne diese Umrechnung wäre ``0.9`` eine
    Konfidenz von einem Prozent — also eine Eskalation bei nahezu vollständiger
    Sicherheit.
    """
    if value is None:
        raise ValueError("Konfidenz fehlt")
    if isinstance(value, str):
        text = value.strip().rstrip("%").replace(",", ".")
        if not text:
            raise ValueError("Konfidenz fehlt")
        value = float(text)
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError("Konfidenz ist keine Zahl")
    # 0..1 ist die Anteilsschreibweise. Die 1 selbst ist mehrdeutig — sie wird als
    # volle Sicherheit gelesen, nicht als ein Prozent: „1.0" meint nie „1 %".
    if 0 <= number <= 1:
        number *= 100
    return max(0, min(100, int(round(number))))


def threshold_for(agent_config: dict | None, task_metadata: dict | None = None) -> int:
    """Die geltende Schwelle: Auftrag schlägt Agent schlägt Vorgabe.

    Ein einzelner Auftrag darf strenger (oder lockerer) sein als der Agent — eine
    Abrechnung verträgt weniger Raten als eine Zusammenfassung.
    """
    for source in (task_metadata or {}, (agent_config or {}).get(_KEY) or {}):
        raw = source.get("confidence_threshold", source.get("threshold"))
        if raw is None:
            continue
        try:
            return max(0, min(100, int(raw)))
        except (TypeError, ValueError):
            # Ein unlesbarer Wert darf nicht stillschweigend zu „nie fragen"
            # werden — dann lieber die naechste Ebene.
            continue
    return DEFAULT_THRESHOLD


def is_enabled(agent_config: dict | None) -> bool:
    """Konfidenz-Routing ist **an**, sofern niemand es abschaltet.

    Andersherum wäre es eine Sicherung, die man erst einschalten muss — und genau
    die bleibt aus.
    """
    raw = ((agent_config or {}).get(_KEY) or {}).get("enabled", True)
    if isinstance(raw, str):
        return raw.strip().lower() not in ("", "0", "false", "no", "nein", "off")
    return bool(raw)


def should_escalate(confidence: int, threshold: int) -> bool:
    """Unter der Schwelle wird gefragt. Genau AUF der Schwelle nicht.

    Sonst hiesse eine Schwelle von 100, dass immer gefragt wird — auch bei
    vollständiger Sicherheit.
    """
    return confidence < threshold


def build_question(question: str, confidence: int, threshold: int) -> str:
    """Die Rückfrage mit dem Grund davor.

    Ohne die Zahl steht dort eine Frage ohne Anlass, und der Mensch weiss nicht,
    ob der Agent knapp daneben lag oder im Nebel stocherte.
    """
    return f"[Unsicher: {confidence}% — Schwelle {threshold}%] {question}".strip()
