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

#: Deutsche Anzeige der fremden Skills, die Vorlagen mitbringen (#902):
#: Name -> (Titel, Kurztext). Die Quellen beschreiben ihre Skills englisch; in der
#: Vorlagen-Auswahl liest ein Fachanwender diesen Text statt der Beschreibung der
#: Quelle. ``description`` bleibt unverändert (Suche, Agenten). Mitgelieferte Skills
#: sind schon deutsch und stehen hier nicht. Ein Test prüft, dass jeder fremde
#: Skill aus ``VORLAGEN_SKILLS`` einen Eintrag hat.
SKILL_ANZEIGE: dict[str, tuple[str, str]] = {
    # Dateiformate
    "docx": ("Word-Dokumente", "Word-Dateien erstellen, bearbeiten und auswerten."),
    "xlsx": ("Excel-Tabellen", "Tabellen anlegen, rechnen, auswerten und als Excel-Datei liefern."),
    "pptx": ("PowerPoint-Folien", "Präsentationen als PowerPoint-Datei erstellen und überarbeiten."),
    "pdf": ("PDF-Dokumente", "PDFs lesen, zusammenführen, ausfüllen und erzeugen."),
    # Entwicklung
    "test-driven-development": ("Testgetriebene Entwicklung", "Erst den Test schreiben, dann den Code — für jede Funktion und jeden Fehler."),
    "systematic-debugging": ("Fehler systematisch suchen", "Ursachen von Fehlern Schritt für Schritt eingrenzen, bevor etwas geändert wird."),
    "verification-before-completion": ("Prüfen vor dem Abschluss", "Erst nachweisen, dass es funktioniert, dann „fertig“ melden."),
    "frontend-design": ("Oberflächen gestalten", "Ansprechende, durchdachte Weboberflächen entwerfen und umsetzen."),
    "vercel-react-best-practices": ("React und Next.js sauber umsetzen", "Bewährte Regeln für schnelle, wartbare React- und Next.js-Anwendungen."),
    "webapp-testing": ("Webanwendungen testen", "Weboberflächen im Browser automatisch durchklicken und prüfen."),
    "api-and-interface-design": ("Schnittstellen entwerfen", "Klare, stabile Programmierschnittstellen und Datenformate entwerfen."),
    "security-and-hardening": ("Absichern und härten", "Anwendungen gegen Angriffe absichern und Schwachstellen schließen."),
    "mcp-builder": ("MCP-Server bauen", "Eigene MCP-Server entwickeln, über die Agenten neue Werkzeuge bekommen."),
    "requesting-code-review": ("Code-Prüfung anfordern", "Fertige Änderungen gezielt zur Prüfung vorlegen."),
    "receiving-code-review": ("Code-Prüfung umsetzen", "Hinweise aus einer Prüfung sorgfältig bewerten und umsetzen."),
    "find-bugs": ("Fehler aufspüren", "Änderungen gezielt nach Fehlern und Schwachstellen durchsuchen."),
    "security-review": ("Sicherheitsprüfung", "Code auf Sicherheitslücken prüfen und Funde belegen."),
    "playwright-best-practices": ("Browser-Tests mit Playwright", "Zuverlässige automatische Browser-Tests mit Playwright schreiben."),
    "dogfood": ("Anwendung selbst ausprobieren", "Eine Anwendung wie ein Nutzer durchgehen und Auffälligkeiten melden."),
    "security-best-practices": ("Sicherheitsregeln", "Bewährte Sicherheitsregeln beim Entwickeln einhalten."),
    "security-threat-model": ("Bedrohungsmodell", "Mögliche Angriffswege einer Anwendung erkennen und bewerten."),
    "gha-security-review": ("GitHub-Actions prüfen", "Automatisierungen in GitHub Actions auf Sicherheitslücken prüfen."),
    "docker-project-foundations": ("Docker-Grundlagen", "Projekte sauber für Docker aufsetzen."),
    "docker-compose-patterns": ("Docker Compose", "Mehrere Dienste mit Docker Compose zuverlässig zusammenspielen lassen."),
    "docker-build-strategies": ("Docker-Abbilder bauen", "Docker-Abbilder schlank, schnell und reproduzierbar bauen."),
    "docker-destructive-guardrails": ("Docker ohne Datenverlust", "Gefährliche Docker-Befehle erkennen, bevor Daten verloren gehen."),
    "ci-cd-and-automation": ("Automatisch bauen und ausliefern", "Abläufe für Bauen, Testen und Ausliefern einrichten."),
    "observability-and-instrumentation": ("Überwachung und Protokolle", "Anwendungen mit Messwerten und Protokollen beobachtbar machen."),
    "writing-plans": ("Pläne schreiben", "Vorhaben in klare, überprüfbare Arbeitsschritte zerlegen."),
    "executing-plans": ("Pläne abarbeiten", "Einen schriftlichen Plan Schritt für Schritt umsetzen und abhaken."),
    "agent-browser": ("Browser steuern", "Webseiten öffnen, bedienen und Inhalte auslesen."),
    "postgres": ("PostgreSQL", "Mit PostgreSQL-Datenbanken sicher und effizient arbeiten."),
    "design-postgres-tables": ("Tabellen entwerfen", "Datenbanktabellen in PostgreSQL sinnvoll anlegen."),
    "postgres-database-migration": ("Datenbank migrieren", "Änderungen an der Datenbankstruktur sicher einspielen."),
    "schema-exploration": ("Datenbank erkunden", "Aufbau und Inhalte einer unbekannten Datenbank verstehen."),
    "supabase-postgres-best-practices": ("Supabase und PostgreSQL", "Bewährte Regeln für Datenbanken mit Supabase und PostgreSQL."),
    # Daten & Recherche
    "derive-client": ("Webdaten auslesen", "Aus Webseiten strukturierte Daten gewinnen."),
    # Design & Präsentation
    "ui-ux-pro-max": ("Gestaltung und Bedienbarkeit", "Oberflächen nach Gestaltungs- und Bedienregeln entwerfen."),
    "design-system": ("Gestaltungssystem", "Einheitliche Farben, Schriften und Bausteine festlegen."),
    "web-design-guidelines": ("Regeln für Webgestaltung", "Weboberflächen auf Gestaltung und Barrierefreiheit prüfen."),
    "ui-styling": ("Oberflächen gestalten", "Bausteine einer Oberfläche einheitlich gestalten."),
    "slides": ("Foliensätze", "Präsentationen mit klarer Gliederung und Gestaltung bauen."),
    "theme-factory": ("Designvorlagen", "Fertige Farb- und Schriftvorlagen auf Dokumente und Folien anwenden."),
    "brand": ("Markenauftritt", "Inhalte im Erscheinungsbild der eigenen Marke gestalten."),
    # Text
    "documentation-and-adrs": ("Dokumentation", "Technische Dokumentation und Entscheidungsprotokolle schreiben."),
    "doc-coauthoring": ("Dokumente gemeinsam schreiben", "Längere Dokumente Abschnitt für Abschnitt gemeinsam erarbeiten."),
    "writing-guidelines": ("Schreibregeln", "Klar, knapp und verständlich schreiben."),
    "copywriting": ("Werbetexte", "Überzeugende Texte für Webseiten, Anzeigen und Angebote schreiben."),
    "copy-editing": ("Texte überarbeiten", "Vorhandene Texte straffen, korrigieren und verbessern."),
    "content-strategy": ("Inhaltsstrategie", "Planen, welche Inhalte für wen und wann entstehen."),
    "ai-seo": ("Sichtbarkeit in KI-Suchen", "Inhalte so aufbereiten, dass KI-Suchdienste sie finden und zitieren."),
    # Marketing & Vertrieb
    "marketing-plan": ("Marketingplan", "Ziele, Zielgruppen und Maßnahmen zu einem Plan zusammenführen."),
    "emails": ("E-Mail-Marketing", "Newsletter und E-Mail-Strecken planen und schreiben."),
    "launch": ("Produkteinführung", "Die Einführung eines Produkts oder Angebots planen."),
    "analytics": ("Auswertung", "Kennzahlen festlegen, erfassen und auswerten."),
    "seo-audit": ("SEO-Prüfung", "Webseiten auf Schwächen bei der Suchmaschinenoptimierung prüfen."),
    "programmatic-seo": ("SEO-Seiten in Serie", "Viele gleichartige Seiten für Suchanfragen planen."),
    "schema": ("Strukturierte Daten", "Webseiten mit strukturierten Daten für Suchmaschinen auszeichnen."),
    "site-architecture": ("Seitenstruktur", "Aufbau und Verlinkung einer Website planen."),
    "audit-website": ("Website prüfen", "Eine Website auf Technik, Inhalte und Suchmaschinentauglichkeit prüfen."),
    "social": ("Social Media", "Beiträge für soziale Netzwerke planen und schreiben."),
    "image": ("Bilder", "Bilder für Beiträge und Kampagnen planen und erzeugen."),
    "video": ("Videos", "Kurze Videos für Beiträge und Kampagnen planen."),
    "public-relations": ("Öffentlichkeitsarbeit", "Pressearbeit und Kontakt zu Medien planen."),
    "prospecting": ("Neukunden finden", "Passende Ansprechpartner und Unternehmen recherchieren."),
    "cold-email": ("Erstansprache per E-Mail", "Erste E-Mails an neue Kontakte schreiben."),
    "sales-enablement": ("Vertriebsunterlagen", "Unterlagen und Argumente für den Vertrieb erstellen."),
    "offers": ("Angebote gestalten", "Angebote so zuschneiden, dass sie überzeugen."),
    "competitor-profiling": ("Wettbewerber analysieren", "Steckbriefe von Wettbewerbern erstellen und vergleichen."),
    "customer-research": ("Kunden verstehen", "Bedürfnisse und Meinungen von Kunden auswerten."),
    # Management & Büro
    "dispatching-parallel-agents": ("Arbeit verteilen", "Unabhängige Teilaufgaben gleichzeitig an mehrere Agenten geben."),
    "brainstorming": ("Ideen entwickeln", "Vor dem Umsetzen gemeinsam Ziel, Anforderungen und Lösungsideen klären."),
    "internal-comms": ("Interne Kommunikation", "Mitteilungen, Rundschreiben und Statusberichte für das eigene Haus schreiben."),
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
    from app.core.mitgelieferte_skills import QUELLE

    erlaubt = _erlaubte_herkunft()
    # Der Name einer Quelle allein reicht nicht: Eine vom Admin eingetragene Quelle mit
    # gleichem Ort (z. B. anderer Branch) bekäme sonst dieselbe Herkunft. Öffentliche
    # Skills zählen nur aus den eingebauten Quellen (created_by "import:github").
    def _vertraut(herkunft, erstellt) -> bool:
        if herkunft == QUELLE:
            return True
        return herkunft in erlaubt and (erstellt or "") == "import:github"

    nach_name = {name: sid for sid, name, herkunft, erstellt in rows if _vertraut(herkunft, erstellt)}
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
