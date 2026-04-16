"""
Skill Catalog Crawler - fetches skills from GitHub repos daily and caches in Redis.

Uses GitHub API to discover SKILL.md files in known skill repositories,
parses their frontmatter for metadata, and stores the catalog in Redis.
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

# Repos to crawl for skills. Format: "owner/repo"
SKILL_REPOS = [
    "vercel-labs/skills",
    "vercel-labs/agent-skills",
    "vercel-labs/next-skills",
    "vercel-labs/agent-browser",
    "anthropics/skills",
    "nextlevelbuilder/ui-ux-pro-max-skill",
    "coreyhaines31/marketingskills",
    "obra/superpowers",
    "supabase/agent-skills",
    "remotion-dev/skills",
    "squirrelscan/skills",
]

# Category heuristics based on skill name / description keywords
CATEGORY_KEYWORDS = {
    "design": ["design", "ui", "ux", "css", "style", "visual", "interface", "web-design", "frontend-design"],
    "dev": ["react", "next", "typescript", "debug", "test", "tdd", "postgres", "supabase", "best-practices", "development"],
    "marketing": ["seo", "marketing", "copywriting", "psychology", "brand"],
    "docs": ["pdf", "pptx", "docx", "xlsx", "document", "word", "excel", "powerpoint"],
    "tools": ["browser", "audit", "brainstorm", "plan", "writing-plans"],
    "core": ["find-skills", "skill-creator"],
}

REDIS_KEY = "skill_catalog"
REDIS_TTL = 86400  # 24 hours
CRAWL_INTERVAL = 86400  # 24 hours


def _guess_category(name: str, description: str) -> str:
    """Guess a skill category from its name and description."""
    text = f"{name} {description}".lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                return category
    return "tools"


def _parse_frontmatter(content: str) -> dict:
    """Parse YAML-like frontmatter from SKILL.md content."""
    match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return {}
    frontmatter = {}
    for line in match.group(1).strip().split("\n"):
        if ":" in line:
            key, _, value = line.partition(":")
            frontmatter[key.strip()] = value.strip().strip('"').strip("'")
    return frontmatter


class SkillCrawlerService:
    """Crawls GitHub repos for skills and caches the catalog in Redis."""

    def __init__(self, redis_service):
        self.redis = redis_service
        self._repos = list(SKILL_REPOS)

    async def run(self):
        """Background loop - crawl immediately on startup, then every 24h."""
        while True:
            try:
                await self.crawl()
            except Exception as e:
                logger.error(f"Skill crawler error: {e}")
            await asyncio.sleep(CRAWL_INTERVAL)

    async def crawl(self) -> list[dict]:
        """Crawl all repos and cache the catalog."""
        logger.info("Skill crawler: starting crawl of %d repos", len(self._repos))
        all_skills = []

        async with httpx.AsyncClient(timeout=30.0) as client:
            tasks = [self._crawl_repo(client, repo) for repo in self._repos]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        for repo, result in zip(self._repos, results):
            if isinstance(result, Exception):
                logger.warning(f"Failed to crawl {repo}: {result}")
                continue
            all_skills.extend(result)

        # Deduplicate by name (first occurrence wins)
        seen = set()
        unique = []
        for skill in all_skills:
            if skill["name"] not in seen:
                seen.add(skill["name"])
                unique.append(skill)

        # Sort by name
        unique.sort(key=lambda s: s["name"])

        # Cache in Redis
        payload = {
            "skills": unique,
            "crawled_at": datetime.now(timezone.utc).isoformat(),
            "repo_count": len(self._repos),
            "skill_count": len(unique),
        }
        if self.redis.client:
            await self.redis.client.set(
                REDIS_KEY,
                json.dumps(payload),
                ex=REDIS_TTL,
            )

        # Sync to DB (skill marketplace)
        await self._sync_to_db(unique)

        logger.info(
            "Skill crawler: found %d skills across %d repos",
            len(unique),
            len(self._repos),
        )
        return unique

    async def _crawl_repo(self, client: httpx.AsyncClient, repo: str) -> list[dict]:
        """Crawl a single GitHub repo for SKILL.md files."""
        skills = []
        headers = {"Accept": "application/vnd.github.v3+json"}

        # Get the full file tree
        tree_url = f"https://api.github.com/repos/{repo}/git/trees/main?recursive=1"
        resp = await client.get(tree_url, headers=headers)

        # Try 'master' branch if 'main' returns 404
        if resp.status_code == 404:
            tree_url = f"https://api.github.com/repos/{repo}/git/trees/master?recursive=1"
            resp = await client.get(tree_url, headers=headers)

        if resp.status_code != 200:
            logger.debug(f"Skipping {repo}: HTTP {resp.status_code}")
            return []

        tree = resp.json().get("tree", [])

        # Find all SKILL.md files
        skill_paths = [
            item["path"]
            for item in tree
            if item["type"] == "blob" and item["path"].endswith("SKILL.md")
        ]

        for path in skill_paths:
            try:
                skill = await self._fetch_skill(client, repo, path, headers)
                if skill:
                    skills.append(skill)
            except Exception as e:
                logger.debug(f"Failed to fetch {repo}/{path}: {e}")

        return skills

    async def _fetch_skill(
        self, client: httpx.AsyncClient, repo: str, path: str, headers: dict
    ) -> dict | None:
        """Fetch and parse a single SKILL.md file."""
        raw_url = f"https://raw.githubusercontent.com/{repo}/main/{path}"
        resp = await client.get(raw_url)
        if resp.status_code == 404:
            raw_url = f"https://raw.githubusercontent.com/{repo}/master/{path}"
            resp = await client.get(raw_url)
        if resp.status_code != 200:
            return None

        content = resp.text
        frontmatter = _parse_frontmatter(content)

        # Derive skill name from path: e.g. "skills/find-skills/SKILL.md" -> "find-skills"
        parts = path.replace("SKILL.md", "").strip("/").split("/")
        name = frontmatter.get("name") or (parts[-1] if parts[-1] else parts[-2] if len(parts) > 1 else repo.split("/")[-1])

        description = frontmatter.get("description", "")
        if not description:
            # Try to get first non-frontmatter line as description
            body = re.sub(r"^---.*?---\s*", "", content, flags=re.DOTALL).strip()
            first_line = body.split("\n")[0].strip().lstrip("# ").strip()
            description = first_line[:120] if first_line else f"Skill from {repo}"

        category = _guess_category(name, description)

        # Extract body (everything after frontmatter)
        body = re.sub(r"^---.*?---\s*", "", content, flags=re.DOTALL).strip()

        return {
            "name": name,
            "repo": repo,
            "description": description,
            "category": category,
            "content": body,
        }

    async def _sync_to_db(self, skills: list[dict]) -> None:
        """Sync crawled skills to the DB marketplace (upsert by name)."""
        try:
            from app.db.session import async_session_factory
            from app.models.skill import Skill, SkillStatus
            from sqlalchemy import select

            async with async_session_factory() as db:
                imported = 0
                for s in skills:
                    existing = (await db.execute(
                        select(Skill).where(Skill.name == s["name"])
                    )).scalar_one_or_none()

                    if existing:
                        # Update description/content if changed
                        if s.get("content") and s["content"] != existing.content:
                            existing.content = s["content"]
                            existing.description = s.get("description", existing.description)
                    else:
                        skill = Skill(
                            name=s["name"],
                            description=s.get("description", ""),
                            content=s.get("content", ""),
                            category=s.get("category", "tool"),
                            status=SkillStatus.ACTIVE,
                            created_by=f"import:github",
                            source_repo=s.get("repo"),
                            source_url=f"https://github.com/{s['repo']}" if s.get("repo") else None,
                        )
                        db.add(skill)
                        imported += 1

                await db.commit()
                if imported:
                    logger.info(f"Skill crawler: imported {imported} new skills to DB")
        except Exception as e:
            logger.warning(f"Skill crawler: DB sync failed: {e}")

    async def get_catalog(self) -> dict | None:
        """Get cached catalog from Redis."""
        if not self.redis.client:
            return None
        data = await self.redis.client.get(REDIS_KEY)
        if data:
            return json.loads(data)
        return None
