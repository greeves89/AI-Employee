"""Rollen-Vorlagen: fertige Rechtebündel zum Auswählen und Anpassen (Enterprise-Rollenmodell).

Eine Vorlage ist KEIN eigenes Rechtesystem. Sie füllt eine eigene Rolle (``CustomRole``) mit
Werten, die ``core/permissions`` ohnehin durchsetzt — Anzahl Agenten, Autonomie-Grenze,
Menüpunkte, Integrationen, MCP-Server, Secrets. Danach ist es eine ganz normale Gruppe: Der
Admin passt sie an (Modelle, Vorlagen, Secrets freigeben) und ordnet sie einer Entra-Gruppe zu.

``grundrolle`` ist die Empfehlung für ``user.role`` (Admin, Manager, Mitglied, Betrachter). Die
Grundrolle entscheidet über die großen Schranken (z. B. Verwaltung), die Gruppe über das Bündel.
"""

from __future__ import annotations

from typing import Any

#: Menüpunkte für Menschen, die mit Agenten arbeiten (keine Verwaltung).
_ARBEIT = ["/heute", "/agents", "/tasks", "/projekte", "/approvals", "/files", "/knowledge",
           "/besprechungen", "/chat", "/help", "/settings"]

VORLAGEN: dict[str, dict[str, Any]] = {
    "teamleitung": {
        "name": "Teamleitung",
        "grundrolle": "manager",
        "beschreibung": "Führt ein Team mit Agenten: mehr Agenten, Freigaben und Projekte im Team, "
                        "keine Plattform-Einstellungen.",
        "rechte": {"max_agents": 20, "max_autonomy_level": "l3"},
    },
    "mitarbeiter": {
        "name": "Mitarbeiter",
        "grundrolle": "member",
        "beschreibung": "Standard für alle: wenige Agenten, nur freigegebene Vorlagen, Modelle und "
                        "MCP-Server, Agenten fragen vor Wirkung nach außen.",
        "rechte": {"max_agents": 3, "max_autonomy_level": "l3", "menu_paths": _ARBEIT},
    },
    "entwickler": {
        "name": "Entwickler",
        "grundrolle": "member",
        "beschreibung": "Software-Teams: mehr Agenten, Projekte mit Git, Agenten dürfen in "
                        "Projekten selbst handeln.",
        "rechte": {"max_agents": 10, "max_autonomy_level": "l4",
                   "menu_paths": _ARBEIT + ["/apps", "/skills", "/workflows"]},
    },
    "betrachter": {
        "name": "Betrachter",
        "grundrolle": "viewer",
        "beschreibung": "Nur lesen: Agenten, Aufgaben und Projekte ansehen, nichts anlegen oder "
                        "ändern (z. B. Revision, Datenschutz, Management).",
        "rechte": {"max_agents": 0, "max_autonomy_level": "l1",
                   "menu_paths": ["/heute", "/dashboard", "/agents", "/tasks", "/projekte", "/help"],
                   "integration_providers": [], "mcp_server_ids": [], "secret_ids": [],
                   "ai_account_ids": []},
    },
    "gast": {
        "name": "Gast",
        "grundrolle": "member",
        "beschreibung": "Externe Partner: keine eigenen Agenten, keine Integrationen, keine "
                        "MCP-Server oder Secrets — arbeitet nur mit Agenten, die ihm freigegeben werden.",
        "rechte": {"max_agents": 0, "max_autonomy_level": "l1",
                   "menu_paths": ["/heute", "/agents", "/tasks", "/help"],
                   "integration_providers": [], "mcp_server_ids": [], "secret_ids": [],
                   "ai_account_ids": [], "url_host_patterns": []},
    },
}

#: Plattform-Admin ist keine Gruppe, sondern die Grundrolle „Admin“ (immer unbeschränkt).
PLATTFORM_ADMIN = {
    "schluessel": "plattform_admin", "name": "Plattform-Admin", "grundrolle": "admin",
    "beschreibung": "IT: alles, auch Einstellungen, Updates, Sicherheit. Keine Gruppe — Grundrolle "
                    "„Admin“ am Nutzer setzen (bzw. Entra-Gruppe auf „Admin“ abbilden).",
}


def rechte_fuer(schluessel: str) -> dict[str, Any]:
    """Vollständiges Rechtebündel einer Vorlage: die Standardwerte ihrer Grundrolle, darüber die
    Vorlage. In einer eigenen Rolle heißt ein FEHLENDER Schlüssel „ohne Grenze“ — ohne diese
    Grundlage wäre ein „Betrachter“ bei Modellen, Vorlagen und Adressen weiter gefasst als die
    Grundrolle Betrachter (Review 13.11.2026)."""
    from app.core.permissions import DEFAULT_PERMISSIONS_BY_ROLE
    from app.models.user import UserRole

    vorlage = VORLAGEN[schluessel]
    basis = DEFAULT_PERMISSIONS_BY_ROLE[UserRole(vorlage["grundrolle"])]
    rechte = {k: (list(v) if isinstance(v, list) else v) for k, v in basis.items()}
    rechte.update({k: (list(v) if isinstance(v, list) else v) for k, v in vorlage["rechte"].items()})
    return rechte


def liste() -> list[dict]:
    """Für die Oberfläche: alle Vorlagen samt Plattform-Admin (nur zur Erklärung)."""
    return [PLATTFORM_ADMIN] + [
        {"schluessel": k, "name": v["name"], "grundrolle": v["grundrolle"],
         "beschreibung": v["beschreibung"], "rechte": rechte_fuer(k)}
        for k, v in VORLAGEN.items()
    ]


def rechte_kurz(p: dict[str, Any]) -> dict[str, Any]:
    """Wirksame Rechte, lesbar zusammengefasst (Rechte-Übersicht). ``None`` = ohne Grenze,
    sonst Anzahl freigegebener Einträge. KI-Konten und Secrets: ``None`` heißt hier KEINE
    (Standard-Sperre, siehe core/permissions)."""
    def anzahl(schluessel: str, none_heisst: str = "alle"):
        wert = p.get(schluessel)
        return none_heisst if wert is None else len(wert)

    return {
        "agenten": "unbegrenzt" if p.get("max_agents") is None else p.get("max_agents"),
        "autonomie": "unbegrenzt" if p.get("max_autonomy_level") is None else str(p["max_autonomy_level"]).upper(),
        "modelle": anzahl("models"),
        "vorlagen": anzahl("template_ids"),
        "mcp_server": anzahl("mcp_server_ids", "für alle freigegebene"),
        "integrationen": anzahl("integration_providers"),
        "ki_konten": anzahl("ai_account_ids", "keine"),
        "secrets": anzahl("secret_ids", "keine"),
        "menue": anzahl("menu_paths"),
    }
