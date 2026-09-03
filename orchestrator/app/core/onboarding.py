"""Einrichtung eines Agenten — EINE Wahrheit statt zwei.

Bisher gab es zwei Staende, die sich widersprachen:
  * ``agent.config['onboarding_complete']`` in der DB — beim Anlegen gesetzt (false fuer
    claude_code, true fuer alles andere) und danach von NIEMANDEM je umgeschaltet. Trieb
    nur das Abzeichen in der Agentenliste.
  * die Kopfzeile ``## Onboarding Status: NOT COMPLETED`` in ``/workspace/knowledge.md``,
    die der Agent selbst pflegen sollte.

Beim Kunden stand in der DB „fertig" und in der Datei „nicht fertig" — die Agenten hielten
darum jeden proaktiven Lauf an, waehrend die Oberflaeche sie als eingerichtet zeigte.
493 Laeufe, 51 USD, kein einziges Arbeitsergebnis.

Ab jetzt gilt die DB, und sie wird ueber ``complete_onboarding`` gesetzt — dasselbe
Werkzeug schreibt die im Gespraech genannten Aufgaben direkt als Verantwortungsbereiche
weg. Das Einrichtungsgespraech ERZEUGT damit die Struktur, aus der sich der Agent
anschliessend seinen Tag baut, statt sie nur in Prosa abzulegen.
"""

from app.core.responsibilities import validated_responsibilities


def is_onboarded(agent) -> bool:
    """Ist dieser Agent eingerichtet? Fehlt der Eintrag, gilt: nein."""
    return bool(((agent.config or {}) if agent else {}).get("onboarding_complete", False))


def has_duties(agent) -> bool:
    """Hat er Verantwortungsbereiche? Ohne sie kann er sich keinen Tag bauen."""
    cfg = (agent.config or {}) if agent else {}
    return bool((cfg.get("proactive") or {}).get("responsibilities"))


def onboarding_note(agent, *, spoken: bool = False) -> str:
    """Prompt-Block zu fehlenden Daueraufgaben, oder "" wenn welche hinterlegt sind.

    Das Einrichtungsgespraech ist entfallen — der Agent haelt sich an seine Vorlage.
    Uebrig bleibt die einzige Luecke, die ihn wirklich lahmlegt: ohne
    Verantwortungsbereiche kann er sich keinen Tag bauen, und der Zeitplaner
    ueberspringt seine proaktiven Laeufe.

    ``spoken=True`` liefert die Fassung fuer den Sprachfront: kuerzer, in der Ich-Form,
    ohne Werkzeugnamen zum Vorlesen.
    """
    if agent is None or has_duties(agent):
        return ""

    if spoken:
        return (
            "\n=== DIR FEHLEN NOCH DAUERAUFGABEN ===\n"
            "Du bist eingerichtet, hast aber keine Verantwortungsbereiche — du wartest also auf "
            "Zuruf, statt dir den Tag selbst zu bauen. Frag den Nutzer bei passender Gelegenheit, "
            "welche wiederkehrenden Aufgaben du uebernehmen sollst, und sichere sie mit "
            "`complete_onboarding`.\n=== ENDE ===\n"
        )

    return (
        "\n=== KEINE VERANTWORTUNGSBEREICHE HINTERLEGT ===\n"
        "Du bist eingerichtet, hast aber keine Daueraufgaben — dein Tagesplan kann deshalb nur\n"
        "aus fremden Todos bestehen. Frag deinen Ansprechpartner mit `notify_user`\n"
        "(is_checkin: true), welche wiederkehrenden Aufgaben du uebernehmen sollst, mach einen\n"
        "konkreten Vorschlag aus dem, was du ueber ihn weisst, und sichere die Antwort mit\n"
        "`complete_onboarding`.\n=== ENDE ===\n"
    )


def apply_completion(agent, *, role: str = "", boundaries: str = "",
                     responsibilities: list[dict] | None = None,
                     notes: str = "") -> dict:
    """Einrichtung abschliessen: Status setzen, Rolle/Grenzen und Bereiche uebernehmen.

    Gibt die neue ``config`` zurueck — der Aufrufer speichert sie (und muss
    ``flag_modified`` setzen, weil es ein JSON-Feld ist).
    """
    config = dict(agent.config or {})
    config["onboarding_complete"] = True
    if role.strip():
        config["role"] = role.strip()[:500]
    if boundaries.strip():
        config["boundaries"] = boundaries.strip()[:2000]

    duties = validated_responsibilities(responsibilities or [])
    proactive = dict(config.get("proactive") or {})
    if duties:
        # Bestehende Bereiche NICHT ueberschreiben — ein zweites Einrichtungsgespraech
        # ergaenzt, es loescht nicht, was jemand von Hand gepflegt hat.
        existing = proactive.get("responsibilities") or []
        known = {str(d.get("title", "")).casefold() for d in existing}
        proactive["responsibilities"] = existing + [
            d for d in duties if d["title"].casefold() not in known
        ]
    if notes.strip():
        addition = notes.strip()[:1000]
        prior = (proactive.get("custom_instructions") or "").strip()
        proactive["custom_instructions"] = f"{prior}\n{addition}".strip() if prior else addition
    config["proactive"] = proactive
    return config
