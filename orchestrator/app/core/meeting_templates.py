"""Vorlagen für Besprechungen (#14).

Eine Besprechung ohne Ablauf wird ein Rundgespräch: alle sagen etwas zum Thema, am
Ende steht eine Zusammenfassung, aber niemand hat entschieden. Die Stufen-Konfiguration
(``stages_config``) konnte das längst steuern — nur musste sie jedes Mal von Hand
zusammengestellt werden, und deshalb tat es niemand.

Hier stehen die vier Abläufe, die in der Praxis vorkommen. Sie sind bewusst knapp: eine
Vorlage mit acht Stufen liest kein Mensch nach, und jede Stufe kostet eine volle Runde
durch alle Teilnehmer.

Eine Vorlage ist nichts als ein vorgefertigtes ``stages_config`` — es entsteht kein
zweiter Mechanismus, und wer will, kann die Stufen nach dem Anlegen weiter anpassen.
"""

# Die zulaessigen Schwerpunkte einer Stufe. `focus` steuert, worauf der
# Teilnehmer-Prompt in dieser Runde draengt.
FOCUS_RESEARCH = "research"
FOCUS_DISCUSS = "discuss"
FOCUS_DECIDE = "decide"
FOCUS_PLAN = "plan"

TEMPLATES: dict[str, dict] = {
    "daily": {
        "label": "Daily",
        "description": "Kurz und taeglich: Stand, Hindernisse, naechster Schritt.",
        "max_rounds": 3,
        "use_moderator": False,
        "stages": [
            {"name": "Stand", "rounds": 1, "focus": FOCUS_RESEARCH},
            {"name": "Hindernisse", "rounds": 1, "focus": FOCUS_DISCUSS},
            {"name": "Naechster Schritt", "rounds": 1, "focus": FOCUS_PLAN},
        ],
    },
    "retro": {
        "label": "Retrospektive",
        "description": "Was lief gut, was nicht, und was wird konkret geaendert.",
        "max_rounds": 4,
        # Ohne Moderator wiederholt eine Retro die Beschwerden, statt sie zu buendeln.
        "use_moderator": True,
        "stages": [
            {"name": "Was lief gut", "rounds": 1, "focus": FOCUS_RESEARCH},
            {"name": "Was lief nicht", "rounds": 1, "focus": FOCUS_RESEARCH},
            {"name": "Ursachen", "rounds": 1, "focus": FOCUS_DISCUSS},
            {"name": "Eine Aenderung", "rounds": 1, "focus": FOCUS_DECIDE},
        ],
    },
    "workshop": {
        "label": "Workshop",
        "description": "Breit sammeln, dann verdichten und ein Ergebnis bauen.",
        "max_rounds": 8,
        "use_moderator": True,
        # Der einzige Ablauf mit echtem Artefakt: ein Workshop ohne Ergebnis ist ein
        # laengeres Gespraech.
        "deliverable": True,
        "stages": [
            {"name": "Sammeln", "rounds": 2, "focus": FOCUS_RESEARCH},
            {"name": "Verdichten", "rounds": 2, "focus": FOCUS_DISCUSS},
            {"name": "Auswaehlen", "rounds": 1, "focus": FOCUS_DECIDE},
            {"name": "Ausarbeiten", "rounds": 3, "focus": FOCUS_PLAN},
        ],
    },
    "entscheidung": {
        "label": "Entscheidung",
        "description": "Optionen gegeneinander abwaegen und eine waehlen.",
        "max_rounds": 4,
        "use_moderator": True,
        "stages": [
            {"name": "Optionen", "rounds": 1, "focus": FOCUS_RESEARCH},
            {"name": "Dafuer und dagegen", "rounds": 2, "focus": FOCUS_DISCUSS},
            {"name": "Entscheidung", "rounds": 1, "focus": FOCUS_DECIDE},
        ],
    },
}


def list_templates() -> list[dict]:
    """Alle Vorlagen für die Auswahl in der Oberfläche."""
    return [
        {
            "id": key,
            "label": tpl["label"],
            "description": tpl["description"],
            "stages": tpl["stages"],
            "max_rounds": tpl["max_rounds"],
            "use_moderator": tpl["use_moderator"],
            "deliverable": tpl.get("deliverable", False),
            # Wie lange das ungefaehr dauert, gemessen in Wortmeldungen: Stufen mal
            # Runden mal Teilnehmer. Ohne diese Angabe waehlt jemand "Workshop" fuer
            # eine Frage, die in einer Runde beantwortet waere.
            "total_rounds": sum(s["rounds"] for s in tpl["stages"]),
        }
        for key, tpl in TEMPLATES.items()
    ]


def get_template(template_id: str) -> dict | None:
    return TEMPLATES.get((template_id or "").strip().lower())


def apply_template(template_id: str, overrides: dict | None = None) -> dict | None:
    """Die Felder, mit denen ein Raum aus dieser Vorlage angelegt wird.

    Ausdrückliche Angaben des Aufrufers gewinnen: die Vorlage ist ein Startpunkt,
    keine Zwangsjacke.
    """
    tpl = get_template(template_id)
    if tpl is None:
        return None
    out = {
        "stages_config": [dict(s) for s in tpl["stages"]],
        "max_rounds": tpl["max_rounds"],
        "use_moderator": tpl["use_moderator"],
        "deliverable": tpl.get("deliverable", False),
    }
    for key, value in (overrides or {}).items():
        if value is not None:
            out[key] = value
    return out
