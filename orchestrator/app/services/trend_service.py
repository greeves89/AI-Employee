"""Trend-Driven Skill Auto-Discovery.

Scans GitHub Search API and Hacker News daily for trending AI/agent/MCP repos.
For each new repo, fetches the README and uses Claude to generate a SKILL.md.
Generated skills are saved as DRAFT (needs user review) in the skill catalog.
"""

import logging
from datetime import datetime, timedelta, timezone

import httpx

from app.db.session import async_session_factory
from app.models.skill import Skill, SkillCategory, SkillStatus

logger = logging.getLogger(__name__)

_GITHUB_SEARCH_QUERIES = [
    "mcp server claude topic:mcp",
    "ai agent framework topic:ai-agent",
    "claude tool use topic:claude",
    "llm agent tool topic:llm",
]

_HN_API = "https://hn.algolia.com/api/v1/search"
_GH_SEARCH = "https://api.github.com/search/repositories"
_GH_README = "https://api.github.com/repos/{repo}/readme"

_MIN_STARS = 50
_MIN_HN_POINTS = 80
_README_MAX_CHARS = 6000


class TrendService:
    def __init__(self, redis_service=None, github_token: str | None = None):
        self.redis = redis_service
        self._github_token = github_token
        self._last_run: datetime | None = None

    def _gh_headers(self) -> dict:
        h = {"Accept": "application/vnd.github.v3+json"}
        if self._github_token:
            h["Authorization"] = f"Bearer {self._github_token}"
        return h

    async def tick(self) -> dict:
        """Run a trend scan if 24h have passed since the last run."""
        now = datetime.now(timezone.utc)
        if self._last_run and (now - self._last_run) < timedelta(hours=23):
            return {"skipped": True}
        self._last_run = now
        return await self.scan()

    async def scan(self) -> dict:
        """Fetch trending repos and generate skills for new ones."""
        repos = await self._collect_trending_repos()
        existing = await self._get_existing_source_repos()
        new_repos = [r for r in repos if r["full_name"] not in existing]

        generated = 0
        errors = 0
        for repo in new_repos[:10]:  # cap at 10 per run to avoid token flood
            try:
                skill = await self._generate_skill_for_repo(repo)
                if skill:
                    await self._save_skill(skill)
                    generated += 1
                    logger.info(f"[TrendService] Generated skill for {repo['full_name']}")
            except Exception as e:
                logger.warning(f"[TrendService] Failed for {repo['full_name']}: {e}")
                errors += 1

        if generated > 0:
            await self._notify(generated)

        return {"scanned": len(repos), "new": len(new_repos), "generated": generated, "errors": errors}

    async def _collect_trending_repos(self) -> list[dict]:
        """Fetch repos from GitHub Search + HN Algolia."""
        repos: dict[str, dict] = {}
        since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")

        async with httpx.AsyncClient(timeout=15) as client:
            # GitHub Search
            for q in _GITHUB_SEARCH_QUERIES:
                try:
                    resp = await client.get(
                        _GH_SEARCH,
                        params={"q": f"{q} pushed:>{since}", "sort": "stars", "per_page": 10},
                        headers=self._gh_headers(),
                    )
                    if resp.status_code == 200:
                        for item in resp.json().get("items", []):
                            if item.get("stargazers_count", 0) >= _MIN_STARS:
                                repos[item["full_name"]] = {
                                    "full_name": item["full_name"],
                                    "description": item.get("description") or "",
                                    "stars": item.get("stargazers_count", 0),
                                    "url": item.get("html_url", ""),
                                    "language": item.get("language") or "",
                                }
                except Exception as e:
                    logger.debug(f"[TrendService] GitHub search error ({q}): {e}")

            # Hacker News via Algolia
            try:
                resp = await client.get(
                    _HN_API,
                    params={"query": "github.com AI agent MCP claude", "tags": "story", "hitsPerPage": 20},
                )
                if resp.status_code == 200:
                    for hit in resp.json().get("hits", []):
                        if hit.get("points", 0) < _MIN_HN_POINTS:
                            continue
                        url = hit.get("url", "")
                        if "github.com/" not in url:
                            continue
                        # Extract owner/repo from GitHub URL
                        parts = url.replace("https://github.com/", "").split("/")
                        if len(parts) >= 2:
                            full_name = f"{parts[0]}/{parts[1]}"
                            if full_name not in repos:
                                repos[full_name] = {
                                    "full_name": full_name,
                                    "description": hit.get("title", ""),
                                    "stars": 0,
                                    "url": f"https://github.com/{full_name}",
                                    "language": "",
                                }
            except Exception as e:
                logger.debug(f"[TrendService] HN error: {e}")

        return list(repos.values())

    async def _get_existing_source_repos(self) -> set[str]:
        """Return set of source_repos already in DB to avoid duplicates."""
        from sqlalchemy import select
        async with async_session_factory() as db:
            result = await db.execute(select(Skill.source_repo).where(Skill.source_repo.isnot(None)))
            return {row[0] for row in result.all()}

    async def _fetch_readme(self, client: httpx.AsyncClient, repo: str) -> str | None:
        """Fetch and decode the README for a repo."""
        import base64
        try:
            resp = await client.get(_GH_README.format(repo=repo), headers=self._gh_headers())
            if resp.status_code != 200:
                return None
            data = resp.json()
            content = data.get("content", "")
            decoded = base64.b64decode(content).decode("utf-8", errors="replace")
            return decoded[:_README_MAX_CHARS]
        except Exception:
            return None

    async def _generate_skill_for_repo(self, repo: dict) -> dict | None:
        """Create a stub skill entry for a trending repo.

        Full SKILL.md generation happens later via an agent task (OAuth-compatible).
        The stub surfaces the repo in the Ausstehend tab so users can review and trigger generation.
        """
        async with httpx.AsyncClient(timeout=15) as client:
            readme = await self._fetch_readme(client, repo["full_name"])

        # Skip repos with no useful README
        if not readme or len(readme) < 80:
            return None

        # Security: reject READMEs with prompt-injection patterns or suspicious instructions
        readme_lower = readme.lower()
        INJECTION_PATTERNS = [
            "ignore previous instructions",
            "ignore all previous",
            "disregard your instructions",
            "you are now",
            "new instructions:",
            "system prompt",
            "jailbreak",
            "dan mode",
            "execute the following",
            "run the following command",
            "curl | bash",
            "curl | sh",
            "wget | bash",
            "base64 -d |",
            "eval(",
            "exec(",
        ]
        for pattern in INJECTION_PATTERNS:
            if pattern in readme_lower:
                logger.warning(f"[TrendService] Rejected {repo['full_name']}: suspicious pattern '{pattern}'")
                return None

        # Security: skip very new repos with few stars (higher risk, lower value)
        if repo.get("stars", 0) < 100:
            return None

        name = repo["full_name"].split("/")[-1].replace("-", " ").title()
        description = repo["description"] or f"Tool from {repo['full_name']}"

        # Sanitize description — strip any markdown/HTML that could inject content
        import re
        description = re.sub(r"[<>`]", "", description)[:200]

        # Sanitize README excerpt — strip HTML tags and limit length
        safe_readme = re.sub(r"<[^>]+>", "", readme)[:600]

        stub_content = (
            f"---\n"
            f"name: {name}\n"
            f"description: {description}\n"
            f"category: TOOL\n"
            f"source: auto:trending\n"
            f"---\n\n"
            f"## Über dieses Tool\n\n"
            f"{description}\n\n"
            f"**GitHub:** {repo['url']} ({repo['stars']}⭐)\n\n"
            f"## README-Auszug\n\n"
            f"{safe_readme}\n\n"
            f"---\n"
            f"*Dieser Skill wurde automatisch entdeckt. Bitte überprüfen und freigeben oder ablehnen.*"
        )

        return {
            "name": name,
            "description": description,
            "content": stub_content,
            "source_repo": repo["full_name"],
            "source_url": repo["url"],
        }

    async def _save_skill(self, skill: dict) -> None:
        """Persist a generated skill as DRAFT."""
        from sqlalchemy import select
        async with async_session_factory() as db:
            # Skip if name already exists
            existing = await db.scalar(select(Skill).where(Skill.name == skill["name"]))
            if existing:
                return
            db.add(Skill(
                name=skill["name"],
                description=skill["description"],
                content=skill["content"],
                category=SkillCategory.TOOL,
                status=SkillStatus.DRAFT,
                created_by="auto:trending",
                source_repo=skill["source_repo"],
                source_url=skill["source_url"],
                is_public=False,
            ))
            await db.commit()

    async def _notify(self, count: int) -> None:
        """Publish a notification for the user."""
        if not self.redis:
            return
        import json
        try:
            await self.redis.client.publish("notifications:user", json.dumps({
                "type": "trend_skills",
                "title": f"{count} neue Skill{'s' if count > 1 else ''} entdeckt",
                "message": "Trend-Scanner hat neue Tools gefunden. Prüfe den Skill-Marktplatz.",
                "icon": "sparkles",
            }))
        except Exception:
            pass
