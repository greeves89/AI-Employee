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
]


def get_pack(slug: str) -> dict | None:
    """Return a vertical pack definition by slug, or None."""
    for pack in BUILTIN_VERTICAL_PACKS:
        if pack["slug"] == slug:
            return pack
    return None
