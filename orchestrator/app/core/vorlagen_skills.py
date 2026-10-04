"""Welche Skills bringen die mitgelieferten Vorlagen mit? — EINE Stelle.

Skill-IDs sind je Anlage verschieden; deshalb stehen hier NAMEN. Aufgelöst wird
auf der Anlage selbst (``ids_fuer_namen``): beim Start und nach jedem Crawl landet
das Ergebnis in ``agent_templates.skill_ids``, und das Anlegen eines Agenten aus
einer Vorlage weist genau diese Skills zu (``api/templates.py``).

Fehlt ein Skill auf einer Anlage (Crawl noch nicht gelaufen, kein Internet), wird
er still übergangen und beim nächsten Abgleich ergänzt. Mitgelieferte Skills
(``app/skills_mitgeliefert``) sind immer da.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Skills aus den Standardquellen des Crawlers (services/skill_crawler.DEFAULT_SKILL_REPOS),
# auf die sich die Zuordnung verlässt. Ein Test prüft, dass jeder hier verlangte Name
# entweder mitgeliefert oder hier verzeichnet ist.
OEFFENTLICHE_SKILLS: set[str] = {
    # anthropics/skills
    "docx", "xlsx", "pptx", "pdf", "doc-coauthoring", "internal-comms", "frontend-design",
    "webapp-testing", "mcp-builder", "theme-factory", "brand-guidelines", "canvas-design",
    "skill-creator",
    # obra/superpowers
    "brainstorming", "writing-plans", "executing-plans", "test-driven-development",
    "systematic-debugging", "verification-before-completion", "requesting-code-review",
    "receiving-code-review", "dispatching-parallel-agents", "finishing-a-development-branch",
    # coreyhaines31/marketingskills
    "copywriting", "copy-editing", "content-strategy", "social", "seo-audit", "ai-seo",
    "programmatic-seo", "schema", "site-architecture", "marketing-plan", "emails", "launch",
    "analytics", "public-relations", "cold-email", "prospecting", "sales-enablement",
    "offers", "pricing", "competitor-profiling", "customer-research", "image", "video",
    "ads", "ad-creative",
    # nextlevelbuilder/ui-ux-pro-max-skill
    "ui-ux-pro-max", "design-system", "slides", "ui-styling", "brand", "banner-design",
    # vercel-labs/agent-skills, vercel-labs/agent-browser, supabase, squirrelscan
    "web-design-guidelines", "writing-guidelines", "vercel-react-best-practices",
    "agent-browser", "derive-client", "dogfood", "supabase-postgres-best-practices",
    "audit-website",
    # getsentry/skills, openai/skills
    "security-review", "find-bugs", "gha-security-review", "security-best-practices", "security-threat-model",
    # docker/skills
    "docker-project-foundations", "docker-build-strategies", "docker-compose-patterns", "docker-destructive-guardrails",
    # timescale/pg-aiguide
    "postgres", "design-postgres-tables", "postgres-database-migration", "schema-exploration",
    # addyosmani/agent-skills
    "api-and-interface-design", "ci-cd-and-automation", "observability-and-instrumentation",
    "documentation-and-adrs", "security-and-hardening",
    # microsoft/playwright-cli, currents-dev/playwright-best-practices-skill
    "playwright-cli", "playwright-best-practices",
}

VORLAGEN_SKILLS: dict[str, list[str]] = {
    # Entwicklung
    "fullstack-developer": ["test-driven-development", "systematic-debugging", "verification-before-completion",
                            "frontend-design", "vercel-react-best-practices", "webapp-testing"],
    "api-developer": ["api-and-interface-design", "test-driven-development", "systematic-debugging",
                      "verification-before-completion", "security-and-hardening", "mcp-builder"],
    "code-reviewer": ["requesting-code-review", "receiving-code-review", "find-bugs", "security-review",
                      "verification-before-completion"],
    "qa-tester": ["playwright-best-practices", "webapp-testing", "dogfood",
                  "test-driven-development", "systematic-debugging"],
    "security-auditor": ["security-review", "security-best-practices", "security-threat-model", "find-bugs",
                         "gha-security-review", "security-and-hardening"],
    "devops-engineer": ["docker-project-foundations", "docker-compose-patterns", "docker-build-strategies",
                        "docker-destructive-guardrails", "ci-cd-and-automation", "observability-and-instrumentation",
                        "systematic-debugging"],
    "automation-agent": ["writing-plans", "verification-before-completion", "agent-browser", "xlsx"],
    "database-admin": ["postgres", "design-postgres-tables", "postgres-database-migration", "schema-exploration",
                       "supabase-postgres-best-practices"],
    # Daten
    "data-analyst": ["datenanalyse-bericht", "xlsx", "pdf"],
    "crawler-agent": ["agent-browser", "derive-client", "recherche-mit-quellen", "xlsx"],
    # Design & Präsentation
    "ui-designer": ["ui-ux-pro-max", "frontend-design", "design-system", "web-design-guidelines", "ui-styling"],
    "presentation-designer": ["pptx", "slides", "theme-factory", "brand"],
    # Text
    "technical-writer": ["documentation-and-adrs", "doc-coauthoring", "writing-guidelines", "docx", "pdf"],
    "content-writer": ["copywriting", "copy-editing", "content-strategy", "ai-seo", "writing-guidelines"],
    "translator": ["uebersetzen-lokalisieren", "docx"],
    # Marketing & Vertrieb
    "marketing-agent": ["marketing-plan", "content-strategy", "copywriting", "emails", "launch", "analytics"],
    "seo-specialist": ["seo-audit", "ai-seo", "programmatic-seo", "schema", "site-architecture", "audit-website"],
    "social-media-manager": ["social", "content-strategy", "copywriting", "image", "video"],
    "presse": ["pressemitteilung-schreiben", "public-relations", "copy-editing"],
    "sales-agent": ["prospecting", "cold-email", "sales-enablement", "offers", "competitor-profiling"],
    # Management
    "ceo-manager": ["dispatching-parallel-agents", "writing-plans", "anforderungen-schreiben", "meeting-protokoll"],
    "product-manager": ["anforderungen-schreiben", "brainstorming", "writing-plans", "customer-research"],
    "os-agent": ["dispatching-parallel-agents", "writing-plans", "executing-plans"],
    # Büro & Fachabteilungen
    "meeting-agent": ["meeting-protokoll", "docx"],
    "meeting-tasks": ["meeting-protokoll"],
    "research-assistant": ["recherche-mit-quellen", "docx", "pdf"],
    "first-level-support": ["support-antworten", "internal-comms"],
    "legal-assistant": ["vertrag-pruefen", "rechtsfrage-mit-fundstelle", "docx", "pdf"],
    "jura": ["rechtsfrage-mit-fundstelle", "vertrag-pruefen"],
    "recruiter": ["stellenanzeige-und-bewerbung", "docx"],
    "bookkeeper": ["buchhaltung-vorkontieren", "xlsx", "pdf"],
    "payroll-clerk": ["lohnabrechnung-vorbereiten", "xlsx"],
    "quote-clerk": ["angebot-kalkulieren", "xlsx", "docx"],
    "dispatcher": ["disposition-planen", "xlsx"],
}


def vorlagen_je_skill() -> dict[str, list[str]]:
    """Skill-Name -> Anzeigenamen der mitgelieferten Vorlagen, die ihn nutzen.

    Für den Hinweis „von Vorlage X genutzt“ bei Skills und Skill-Quellen (#895);
    dieselbe Zuordnung wie oben, nur umgedreht — keine zweite Pflegestelle.
    """
    from app.core.agent_templates import BUILTIN_TEMPLATES

    anzeige = {t["name"]: t.get("display_name") or t["name"] for t in BUILTIN_TEMPLATES}
    out: dict[str, list[str]] = {}
    for vorlage, namen in VORLAGEN_SKILLS.items():
        for n in namen:
            out.setdefault(n, []).append(anzeige.get(vorlage, vorlage))
    return out


def _erlaubte_herkunft() -> set[str]:
    """Nur Skills dieser Herkunft dürfen an Vorlagen hängen: mitgeliefert oder aus den
    eingebauten Crawler-Quellen. Sonst könnte jemand mit einem gleichnamigen
    selbst angelegten Skill die Vorlagen ALLER Nutzer der Anlage übernehmen.

    Die eingebauten Quellen stehen seit #895 zusätzlich als Zeilen (``system:builtin``)
    in ``skill_sources`` und sind dort abschaltbar. Maßgeblich bleibt trotzdem die feste
    Liste ``DEFAULT_SKILL_REPOS``: Ein Administrator kann die Zeile ändern oder löschen,
    ohne dass damit beliebige Herkunft für Vorlagen zugelassen würde, und schon
    importierte Skills bleiben gültig, auch wenn die Quelle abgeschaltet ist."""
    from app.core.mitgelieferte_skills import QUELLE
    from app.services.skill_crawler import DEFAULT_SKILL_REPOS

    return {QUELLE, *DEFAULT_SKILL_REPOS}


def herkunft_vertraut(herkunft: str | None, erstellt: str | None) -> bool:
    """Skill bekannter Herkunft: mitgeliefert oder aus einer eingebauten Quelle.

    Der Name einer Quelle allein reicht nicht: Eine vom Admin eingetragene Quelle mit
    gleichem Ort (z. B. anderer Branch) bekäme sonst dieselbe Herkunft. Öffentliche
    Skills zählen nur aus den eingebauten Quellen (created_by "import:github").
    Gilt für Vorlagen UND für Skills, die einem Agenten über seine Rolle zufallen."""
    from app.core.mitgelieferte_skills import QUELLE

    if herkunft == QUELLE:
        return True
    return herkunft in _erlaubte_herkunft() and (erstellt or "") == "import:github"


async def ids_fuer_namen(db, namen: list[str]) -> list[int]:
    """IDs der aktiven Skills mit diesen Namen (nur bekannte Herkunft), in der Reihenfolge
    der Namen; Fehlende oder fremder Herkunft werden übergangen."""
    if not namen:
        return []
    from sqlalchemy import select

    from app.models.skill import Skill, SkillStatus

    rows = (await db.execute(
        select(Skill.id, Skill.name, Skill.source_repo, Skill.created_by)
        .where(Skill.name.in_(namen), Skill.status == SkillStatus.ACTIVE)
    )).all()
    nach_name = {name: sid for sid, name, herkunft, erstellt in rows if herkunft_vertraut(herkunft, erstellt)}
    return [nach_name[n] for n in namen if n in nach_name]


async def vorlagen_aktualisieren() -> int:
    """Die Skill-Listen der mitgelieferten Vorlagen auf dieser Anlage auflösen.

    Gibt die Zahl der geänderten Vorlagen zurück. Selbst angelegte Vorlagen bleiben unberührt.
    """
    from sqlalchemy import select

    from app.db.session import resilient_session
    from app.models.agent_template import AgentTemplate

    geaendert = 0
    async with resilient_session() as db:
        vorlagen = (await db.execute(select(AgentTemplate).where(AgentTemplate.is_builtin.is_(True)))).scalars().all()
        for v in vorlagen:
            namen = VORLAGEN_SKILLS.get(v.name)
            if not namen:
                continue
            ids = await ids_fuer_namen(db, namen)
            if list(v.skill_ids or []) != ids:
                v.skill_ids = ids
                geaendert += 1
        await db.commit()
    if geaendert:
        logger.info("Vorlagen-Skills: %d Vorlagen aktualisiert", geaendert)
    return geaendert
