"""API endpoints for the skill catalog (crawled from skills.sh repos + DB marketplace)."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.skill import Skill, SkillStatus

router = APIRouter(prefix="/skills", tags=["skills-catalog"])


@router.get("/catalog")
async def get_skill_catalog(request: Request, db: AsyncSession = Depends(get_db)):
    """Return merged catalog: crawled GitHub skills + DB marketplace skills."""
    # 1. DB marketplace skills (agent-created + user-created, always available)
    db_result = await db.execute(
        select(Skill).where(Skill.status == SkillStatus.ACTIVE)
    )
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
async def refresh_skill_catalog(request: Request):
    """Force a re-crawl of all skill repos."""
    crawler = getattr(request.app.state, "skill_crawler", None)
    if not crawler:
        return {"detail": "Crawler not available"}

    skills = await crawler.crawl()
    return {"detail": f"Refreshed catalog with {len(skills)} skills"}
