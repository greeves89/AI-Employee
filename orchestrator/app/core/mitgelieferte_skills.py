"""Skills, die mit dem Produkt kommen (``app/skills_mitgeliefert/<name>/SKILL.md``).

Für Fachgebiete, für die es keine brauchbaren öffentlichen Skills gibt
(Buchhaltung, Lohn, Recht, Angebot, Disposition …), liefert AI Employee eigene
deutsche Skills mit. Sie stehen auf JEDER Anlage bereit, auch ohne Internet —
der Marktplatz-Crawler braucht GitHub, eine Kundenanlage hat das nicht immer.

Synchronisiert wird beim Start (und nach jedem Crawl): fehlt der Skill, wird er
angelegt; stammt ein vorhandener gleichnamiger Skill von hier, wird sein Inhalt
nachgezogen. Einen gleichnamigen Skill anderer Herkunft (z. B. von einem Agenten
selbst angelegt) fasst dieser Weg NIE an.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

ORDNER = Path(__file__).resolve().parents[1] / "skills_mitgeliefert"
QUELLE = "ai-employee/mitgeliefert"


def laden() -> list[dict]:
    """Alle mitgelieferten Skills als Katalog-Einträge (gleiches Format wie der Crawler)."""
    from app.services.skill_crawler import _guess_category, _parse_frontmatter

    skills: list[dict] = []
    for datei in sorted(ORDNER.glob("*/SKILL.md")):
        roh = datei.read_text(encoding="utf-8")
        kopf = _parse_frontmatter(roh)
        name = (kopf.get("name") or datei.parent.name).strip()
        beschreibung = (kopf.get("description") or "").strip()
        inhalt = re.sub(r"^---.*?---\s*", "", roh, flags=re.DOTALL).strip()
        skills.append({
            "name": name,
            "description": beschreibung,
            "category": _guess_category(name, beschreibung),
            "content": inhalt,
            "source_repo": QUELLE,
            "source_url": None,
            "created_by": "import:mitgeliefert",
            "trusted": True,
        })
    return skills


async def synchronisieren() -> tuple[int, int]:
    """Mitgelieferte Skills in den Marktplatz bringen. Gibt (neu, aktualisiert) zurück."""
    from sqlalchemy import select

    from app.core.skill_security import SkillSecurityError, check_skill_content
    from app.db.session import resilient_session
    from app.models.skill import Skill, SkillCategory, SkillStatus

    neu = aktualisiert = 0
    async with resilient_session() as db:
        for s in laden():
            try:
                check_skill_content(s["content"])
            except SkillSecurityError as e:  # darf nie passieren — Test wacht darüber
                logger.error("Mitgelieferter Skill %s von der Sicherheitsprüfung abgelehnt: %s",
                             s["name"], getattr(e, "reason", e))
                continue
            vorhanden = (await db.execute(select(Skill).where(Skill.name == s["name"]))).scalar_one_or_none()
            if vorhanden is None:
                try:
                    kategorie = SkillCategory((s["category"] or "ROUTINE").upper())
                except ValueError:
                    kategorie = SkillCategory.ROUTINE
                db.add(Skill(
                    name=s["name"], description=s["description"], content=s["content"],
                    category=kategorie, status=SkillStatus.ACTIVE, created_by=s["created_by"],
                    source_repo=QUELLE, source_url=None,
                ))
                neu += 1
            elif vorhanden.source_repo == QUELLE:
                if vorhanden.content != s["content"] or vorhanden.description != s["description"]:
                    vorhanden.content = s["content"]
                    vorhanden.description = s["description"]
                    aktualisiert += 1
            else:
                logger.warning("Mitgelieferter Skill %s: Name schon von %s belegt — nicht angefasst",
                               s["name"], vorhanden.source_repo or vorhanden.created_by)
        await db.commit()
    return neu, aktualisiert
