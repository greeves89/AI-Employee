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
    """Prompt-Block zum Einrichtungsstand, oder "" wenn alles steht.

    ``spoken=True`` liefert die Fassung fuer den Sprachfront: kuerzer, in der Ich-Form,
    ohne Werkzeugnamen zum Vorlesen.
    """
    if agent is None:
        return ""
    onboarded = is_onboarded(agent)
    duties = has_duties(agent)
    if onboarded and duties:
        return ""

    if spoken:
        if not onboarded:
            return (
                "\n=== DU BIST NOCH NICHT EINGERICHTET ===\n"
                "Niemand hat dir bisher gesagt, wofuer du da bist. Sag das dem Nutzer FREUNDLICH "
                "und DIREKT zu Beginn — nicht 'wie kann ich helfen?', sondern sinngemaess: 'Ich "
                "kann dir gern helfen — sag mir nur zuerst, wofuer du mich brauchst.' Frag dann "
                "nach: Welche Rolle? Welche Aufgaben soll ich dauerhaft uebernehmen? Was soll ich "
                "NICHT tun? Sobald du eine Antwort hast, sicherst du sie SOFORT mit "
                "`complete_onboarding` (jede genannte Daueraufgabe als eigenen Bereich). "
                "Danach bestaetigst du in EINEM Satz, was du ab jetzt uebernimmst.\n"
                "=== ENDE ===\n"
            )
        return (
            "\n=== DIR FEHLEN NOCH DAUERAUFGABEN ===\n"
            "Du bist eingerichtet, hast aber keine Verantwortungsbereiche — du wartest also auf "
            "Zuruf, statt dir den Tag selbst zu bauen. Frag den Nutzer bei passender Gelegenheit, "
            "welche wiederkehrenden Aufgaben du uebernehmen sollst, und sichere sie mit "
            "`complete_onboarding`.\n=== ENDE ===\n"
        )

    if not onboarded:
        return (
            "\n=== EINRICHTUNG STEHT AUS (WICHTIG) ===\n"
            "Niemand hat dir bisher gesagt, wofuer du da bist. Halte deswegen NICHT still an und\n"
            "melde auch nicht 'nichts zu tun' — FRAG. Wende dich mit `notify_user`\n"
            "(is_checkin: true) an deinen Ansprechpartner und bitte um genau vier Dinge:\n"
            "  1. Welche Rolle sollst du ausfuellen?\n"
            "  2. Welche Aufgaben uebernimmst du DAUERHAFT (und in welchem Takt)?\n"
            "  3. Was sollst du ausdruecklich NICHT tun?\n"
            "  4. Wer ist dein Ansprechpartner und wann ist er erreichbar?\n"
            "Kommt keine Antwort, fragst du beim naechsten Lauf erneut (die Meldebremse von\n"
            "einmal pro Halbtag gilt weiterhin — lieber einmal taeglich nachhaken als\n"
            "monatelang leer laufen). Sobald die Antwort da ist, rufst du\n"
            "`complete_onboarding` auf: Rolle, Grenzen und JEDE genannte Daueraufgabe als\n"
            "eigenen Verantwortungsbereich. Damit bist du eingerichtet und planst ab dem\n"
            "naechsten Lauf deinen Tag selbst.\n"
            "=== ENDE ===\n"
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
