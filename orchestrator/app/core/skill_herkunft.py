"""Woher stammt ein Skill, und was daran ist heikel? — EINE Stelle (#895).

Die Oberfläche zeigt auf jeder Skill-Karte ein Abzeichen für die Herkunft und
darunter knappe Risiko-Hinweise. Beides wird hier aus den Feldern abgeleitet, die
ein Skill ohnehin trägt (``created_by``, ``source_repo``, Inhalt); es gibt keine
zweite Spalte, die auseinanderlaufen könnte.

Herkunft:
  mitgeliefert   gehört zum Produkt (app/skills_mitgeliefert, Start-Skills)
  eigene Quelle  aus einer Quelle, die ein Administrator eingetragen hat
  öffentlich     aus einer eingebauten oder per Umgebung gesetzten öffentlichen Quelle
  Trend          vom Trend-Scanner entdeckt (nur README-Auszug, Entwurf)
  Agent          von einem Agenten verfasst oder gelernt
  Nutzer         von einem Menschen angelegt
"""

from __future__ import annotations

import re

HERKUNFT_MITGELIEFERT = "mitgeliefert"
HERKUNFT_EIGENE_QUELLE = "eigene Quelle"
HERKUNFT_OEFFENTLICH = "öffentlich"
HERKUNFT_TREND = "Trend"
HERKUNFT_AGENT = "Agent"
HERKUNFT_NUTZER = "Nutzer"

CREATED_BY_TREND = "auto:trending"
PRAEFIX_EIGENE_QUELLE = "import:source:"


def herkunft(created_by: str | None, source_repo: str | None) -> str:
    """Eine der sechs Herkünfte für einen Skill."""
    from app.core.mitgelieferte_skills import QUELLE

    ersteller = created_by or ""
    if source_repo == QUELLE:
        return HERKUNFT_MITGELIEFERT
    if ersteller == CREATED_BY_TREND:
        return HERKUNFT_TREND
    if ersteller.startswith(PRAEFIX_EIGENE_QUELLE):
        return HERKUNFT_EIGENE_QUELLE
    if ersteller.startswith("import:"):
        return HERKUNFT_OEFFENTLICH
    if not ersteller and source_repo:
        return HERKUNFT_OEFFENTLICH  # älterer Katalogeintrag ohne Erstellerangabe
    if ersteller.startswith(("agent:", "reflection:")):
        return HERKUNFT_AGENT
    if ersteller == "system" or ersteller.startswith("system:"):
        return HERKUNFT_MITGELIEFERT
    return HERKUNFT_NUTZER


# Muster, die in einer Anleitung auf etwas Gefährliches hindeuten. Bewusst grob: sie
# sollen einen Menschen vor der Freigabe aufmerksam machen, nichts sperren (gesperrt
# wird ausschließlich durch core/skill_security).
_RISIKO_MUSTER: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(curl|wget)[^\n|]*\|\s*(sudo\s+)?(ba|z)?sh\b", re.I),
     "Lädt Programme aus dem Netz und führt sie sofort aus."),
    (re.compile(r"\brm\s+-[a-z]*r[a-z]*f|\brm\s+-[a-z]*f[a-z]*r", re.I),
     "Enthält Befehle, die Dateien unwiderruflich löschen."),
    (re.compile(r"\bsudo\b", re.I),
     "Verlangt erhöhte Rechte (sudo)."),
    (re.compile(r"base64\s+(-d|--decode)[^\n]*\|\s*(ba|z)?sh\b|\beval\s*\(", re.I),
     "Führt verschleierten oder zusammengesetzten Code aus."),
)


def risiko_hinweise(content: str | None, art: str) -> list[str]:
    """Knappe deutsche Hinweise, worauf bei diesem Skill zu achten ist."""
    hinweise: list[str] = []
    if art == HERKUNFT_TREND:
        hinweise.append("Automatisch entdeckt: nur ein Auszug aus der README, keine geprüfte Anleitung.")
    elif art == HERKUNFT_OEFFENTLICH:
        hinweise.append("Aus einer öffentlichen Quelle, nicht von dir geprüft.")
    elif art == HERKUNFT_AGENT:
        hinweise.append("Von einem Agenten verfasst, nicht gegengelesen.")
    if art == HERKUNFT_MITGELIEFERT or not content:
        return hinweise

    from app.core.skill_security import SkillSecurityError, check_skill_content

    try:
        check_skill_content(content)
    except SkillSecurityError:
        hinweise.append("Enthält ein Installationsskript (package.json) und wird beim Installieren blockiert.")
    for muster, text in _RISIKO_MUSTER:
        if muster.search(content):
            hinweise.append(text)
    return hinweise
