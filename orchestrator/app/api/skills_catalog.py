"""API endpoints for the skill catalog (crawled from skills.sh repos)."""

from fastapi import APIRouter, Request

router = APIRouter(prefix="/skills", tags=["skills-catalog"])


@router.get("/catalog")
async def get_skill_catalog(request: Request):
    """Return the cached skill catalog from the crawler."""
    crawler = getattr(request.app.state, "skill_crawler", None)
    if not crawler:
        return {"skills": [], "crawled_at": None, "repo_count": 0, "skill_count": 0}

    catalog = await crawler.get_catalog()
    if not catalog:
        # No cached data yet - trigger a crawl
        try:
            skills = await crawler.crawl()
            catalog = await crawler.get_catalog()
        except Exception:
            catalog = None

    if not catalog:
        return {"skills": [], "crawled_at": None, "repo_count": 0, "skill_count": 0}

    return catalog


@router.post("/catalog/refresh")
async def refresh_skill_catalog(request: Request):
    """Force a re-crawl of all skill repos."""
    crawler = getattr(request.app.state, "skill_crawler", None)
    if not crawler:
        return {"detail": "Crawler not available"}

    skills = await crawler.crawl()
    return {"detail": f"Refreshed catalog with {len(skills)} skills"}
