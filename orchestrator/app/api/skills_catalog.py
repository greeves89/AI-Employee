"""API endpoints for the skill catalog (crawled from skills.sh repos + DB marketplace)."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import require_admin, require_auth
from app.models.skill import Skill, SkillStatus

router = APIRouter(prefix="/skills", tags=["skills-catalog"])


@router.get("/catalog")
async def get_skill_catalog(request: Request, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Return merged catalog: crawled GitHub skills + DB marketplace skills.

    Nur fuer angemeldete Nutzer — der Katalog lieferte bisher OHNE Anmeldung den
    vollen Inhalt jedes Skills der Anlage, auch der nicht-oeffentlichen und der
    von Agenten gelernten.
    """
    from app.core.ownership import is_admin, visible_agent_ids

    # 1. DB marketplace skills (agent-created + user-created, always available)
    abfrage = select(Skill).where(Skill.status == SkillStatus.ACTIVE)
    if not is_admin(user):
        eigene = [f"user:{user.id}", *[f"agent:{a}" for a in (await visible_agent_ids(user, db) or set())]]
        abfrage = abfrage.where(Skill.is_public.is_(True) | Skill.created_by.in_(eigene))
    db_result = await db.execute(abfrage)
    db_skills = db_result.scalars().all()
    db_entries = [
        {
            "name": s.name,
            "description": s.description,
            "content": s.content,
            "category": s.category.value if hasattr(s.category, "value") else str(s.category),
            "source": s.created_by or "marketplace",
            "source_repo": s.source_repo,
            "avg_rating": s.avg_rating,
            "usage_count": s.usage_count,
            "id": s.id,
            "type": "db",  # signals frontend to use assignment flow, not GitHub clone
        }
        for s in db_skills
    ]

    # 2. Crawled GitHub skills (Redis cache)
    crawler = getattr(request.app.state, "skill_crawler", None)
    crawled_entries = []
    crawled_at = None
    if crawler:
        catalog = await crawler.get_catalog()
        if not catalog:
            try:
                await crawler.crawl()
                catalog = await crawler.get_catalog()
            except Exception:
                catalog = None
        if catalog:
            crawled_entries = catalog.get("skills", [])
            crawled_at = catalog.get("crawled_at")

    # Merge: DB skills first, then crawled (skip duplicates by name)
    db_names = {s["name"] for s in db_entries}
    merged = db_entries + [s for s in crawled_entries if s["name"] not in db_names]

    return {
        "skills": merged,
        "crawled_at": crawled_at,
        "repo_count": len(set(s.get("source_repo") for s in crawled_entries if s.get("source_repo"))),
        "skill_count": len(merged),
        "db_skill_count": len(db_entries),
    }


@router.post("/catalog/refresh")
async def refresh_skill_catalog(request: Request, user=Depends(require_admin)):
    """Force a re-crawl of all skill repos."""
    crawler = getattr(request.app.state, "skill_crawler", None)
    if not crawler:
        return {"detail": "Crawler not available"}

    skills = await crawler.crawl()
    return {"detail": f"Refreshed catalog with {len(skills)} skills"}
