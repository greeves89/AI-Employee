"""Vertical packs — pre-configured industry starter kits (issue #159).

A vertical pack bundles a set of agent templates plus seed knowledge into one
provisioning step, so a new user gets a ready-to-work environment for their
industry in one click instead of assembling agents one by one.

Packs are defined as code constants (not a DB table) — they reference builtin
agent templates by name, so they stay in sync with the template catalog.
"""

# Each pack: slug, name, description, icon, industry, the builtin template
# names to instantiate, optional seed knowledge entries, and an optional first
# demo task. Template names must match entries in agent_templates.BUILTIN_TEMPLATES.
BUILTIN_VERTICAL_PACKS: list[dict] = [
    {
        "slug": "dev-team",
        "name": "Entwickler-Team",
        "description": "Fullstack-Entwicklung, DevOps und Code-Review — ein komplettes "
                       "Software-Team aus Agenten.",
        "icon": "Code2",
        "industry": "tech",
        "template_names": ["fullstack-developer", "devops-engineer", "code-reviewer"],
        "knowledge_entries": [
            {
                "title": "Development Standards",
                "content": "# Development Standards\n\n"
                           "- Conventional Commits (`type(scope): description`).\n"
                           "- Kein Merge ohne grünen Build.\n"
                           "- Tests für jeden neuen Endpoint (happy path + ein Fehlerfall).\n"
                           "- DB-Änderungen immer mit Migration.\n",
                "tags": ["dev", "standards"],
            },
        ],
        "demo_task": {
            "title": "Demo: Repo-Überblick erstellen",
            "prompt": "Verschaffe dir einen Überblick über das Workspace-Repo und "
                      "schreibe eine kurze ARCHITECTURE.md mit den wichtigsten Modulen.",
        },
    },
    {
        "slug": "content-studio",
        "name": "Content-Studio",
        "description": "Technische Doku, Social Media und SEO — Agenten für die "
                       "komplette Content-Produktion.",
        "icon": "PenTool",
        "industry": "marketing",
        "template_names": ["technical-writer", "social-media-manager", "seo-specialist"],
        "knowledge_entries": [
            {
                "title": "Content-Richtlinien",
                "content": "# Content-Richtlinien\n\n"
                           "- Tonalität: klar, freundlich, ohne Buzzwords.\n"
                           "- Jeder Artikel: ein Fokus-Keyword, eine Kernaussage.\n"
                           "- Social-Posts immer mit konkretem Call-to-Action.\n",
                "tags": ["content", "guidelines"],
            },
        ],
        "demo_task": {
            "title": "Demo: Blog-Outline",
            "prompt": "Erstelle eine Gliederung für einen Blog-Artikel über die "
                      "Vorteile von KI-Agenten im Arbeitsalltag.",
        },
    },
    {
        "slug": "support-desk",
        "name": "Support-Desk",
        "description": "Kundensupport, Recherche und Doku — Agenten für ein "
                       "reaktionsschnelles Support-Team.",
        "icon": "Headphones",
        "industry": "support",
        "template_names": ["first-level-support", "research-assistant", "technical-writer"],
        "knowledge_entries": [
            {
                "title": "Support-Playbook",
                "content": "# Support-Playbook\n\n"
                           "- Erst verstehen, dann antworten — Rückfrage bei Unklarheit.\n"
                           "- Bekannte Lösung? Schritt-für-Schritt-Anleitung geben.\n"
                           "- Unklarer/kritischer Fall? Eskalieren statt raten.\n"
                           "- Jede gelöste Anfrage als FAQ-Eintrag festhalten.\n",
                "tags": ["support", "playbook"],
            },
        ],
        "demo_task": {
            "title": "Demo: FAQ-Entwurf",
            "prompt": "Schreibe einen FAQ-Eintrag, der erklärt, wie ein Nutzer sein "
                      "Passwort zurücksetzt.",
        },
    },
    {
        "slug": "steuerkanzlei",
        "name": "Steuerkanzlei",
        "description": "Buchhaltung, Lohn und Fristen — Agenten, die einer Kanzlei "
                       "die Vorarbeit abnehmen. Freigabe bleibt beim Menschen.",
        "icon": "Receipt",
        "industry": "tax",
        "template_names": ["bookkeeper", "payroll-clerk", "legal-assistant"],
        "knowledge_entries": [
            {
                "title": "Arbeitsweise in der Kanzlei",
                "content": "# Arbeitsweise in der Kanzlei\n\n"
                           "- Jede Buchung ist ein **Vorschlag**. Freigabe durch eine "
                           "fachkundige Person, bevor sie verbucht wird.\n"
                           "- Unklarer Beleg kommt in die Rückfragenliste — niemals schätzen.\n"
                           "- Pflichtangaben nach §14 UStG bei jeder Eingangsrechnung prüfen.\n"
                           "- Mandantendaten verlassen die Anlage nicht. Keine externen "
                           "Dienste ohne ausdrückliche Freigabe.\n"
                           "- Kontenrahmen (SKR03/SKR04) hier hinterlegen, bevor gearbeitet wird.\n",
                "tags": ["steuer", "arbeitsweise"],
            },
            {
                "title": "Wiederkehrende Fristen",
                "content": "# Wiederkehrende Fristen\n\n"
                           "- Umsatzsteuer-Voranmeldung: monatlich oder quartalsweise, "
                           "10. des Folgemonats (mit Dauerfristverlängerung 10. des Folgemonats +1).\n"
                           "- Lohnsteuer-Anmeldung: 10. des Folgemonats.\n"
                           "- SV-Beitragsnachweis: fünftletzter Bankarbeitstag des Monats.\n"
                           "- Fristen als terminierte Aufgabe anlegen, nicht nur erwähnen.\n",
                "tags": ["steuer", "fristen"],
            },
        ],
        "demo_task": {
            "title": "Demo: Belegprüfung vorbereiten",
            "prompt": "Lege eine Checkliste an, mit der eine Eingangsrechnung auf die "
                      "Pflichtangaben nach §14 UStG geprüft wird. Ergänze für jede "
                      "Angabe, was zu tun ist, wenn sie fehlt.",
        },
    },
    {
        "slug": "handwerksbetrieb",
        "name": "Handwerksbetrieb",
        "description": "Angebote, Disposition und Kundenkontakt — Agenten für das "
                       "Büro hinter der Baustelle.",
        "icon": "Wrench",
        "industry": "trades",
        "template_names": ["quote-clerk", "dispatcher", "first-level-support"],
        "knowledge_entries": [
            {
                "title": "Angebote und Preise",
                "content": "# Angebote und Preise\n\n"
                           "- Preise kommen **ausschliesslich** aus der hinterlegten "
                           "Preisliste. Fehlt ein Preis, geht die Position mit Hinweis "
                           "in die Rückfragenliste — geschätzte Preise kosten Marge.\n"
                           "- Material, Lohn und Fremdleistung getrennt ausweisen.\n"
                           "- Anfahrt, Entsorgung und Gerüst sind eigene Positionen.\n"
                           "- Nachträge nie stillschweigend einrechnen.\n"
                           "- Jedes Angebot mit Gültigkeitsdauer und Zahlungsbedingungen.\n",
                "tags": ["handwerk", "kalkulation"],
            },
            {
                "title": "Disposition",
                "content": "# Disposition\n\n"
                           "- Ein Termin gilt erst als geplant, wenn er im Kalender steht.\n"
                           "- Fahrzeit und Materialverfügbarkeit gehören in die Planung.\n"
                           "- Verschiebung? Kunde aktiv informieren, mit neuem Zeitfenster.\n"
                           "- Doppelbelegung ist ein Fehler, kein Kompromiss.\n",
                "tags": ["handwerk", "disposition"],
            },
        ],
        "demo_task": {
            "title": "Demo: Angebotsvorlage erstellen",
            "prompt": "Erstelle eine Angebotsvorlage für einen Handwerksbetrieb mit "
                      "Positionsliste (Menge, Einheit, Einzelpreis, Gesamt), getrennt "
                      "nach Material und Lohn, inklusive Gültigkeitsdauer und "
                      "Zahlungsbedingungen.",
        },
    },
]


def get_pack(slug: str) -> dict | None:
    """Return a vertical pack definition by slug, or None."""
    for pack in BUILTIN_VERTICAL_PACKS:
        if pack["slug"] == slug:
            return pack
    return None
