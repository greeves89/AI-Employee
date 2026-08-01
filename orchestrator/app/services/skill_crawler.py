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

# Built-in repos to crawl for skills. Format: "owner/repo".
# Additional sources can be added at runtime via settings.skill_repos
# (env SKILL_REPOS) without a code change — see _configured_repos() (issue #371).
DEFAULT_SKILL_REPOS = [
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

# Backwards-compatible alias (older imports referenced SKILL_REPOS directly).
SKILL_REPOS = DEFAULT_SKILL_REPOS


def _configured_repos() -> list[str]:
    """Built-in defaults plus any repos configured via settings.skill_repos
    (env SKILL_REPOS, comma-separated "owner/repo"). Additive, de-duplicated and
    order-preserving so onboarding a new source needs no code change/release
    (issue #371 phase 1). Empty config → identical to the built-in list."""
    from app.config import settings

    repos = list(DEFAULT_SKILL_REPOS)
    seen = set(repos)
    for entry in (settings.skill_repos or "").split(","):
        entry = entry.strip()
        if entry and entry not in seen:
            seen.add(entry)
            repos.append(entry)
    return repos

# Category heuristics — values MUST match SkillCategory enum (uppercase)
CATEGORY_KEYWORDS = {
    "WORKFLOW": ["design", "ui", "ux", "css", "style", "visual", "interface", "web-design",
                 "react", "next", "typescript", "debug", "test", "tdd", "postgres", "supabase",
                 "best-practices", "development", "seo", "marketing", "copywriting", "brand"],
    "TEMPLATE": ["pdf", "pptx", "docx", "xlsx", "document", "word", "excel", "powerpoint",
                 "template", "report", "format"],
    "TOOL":     ["browser", "audit", "brainstorm", "plan", "writing-plans",
                 "find-skills", "skill-creator", "tool", "grep", "search"],
    "PATTERN":  ["pattern", "architecture", "code", "refactor", "structure"],
    "RECIPE":   ["recipe", "setup", "install", "configure", "deploy", "monitoring"],
}

REDIS_KEY = "skill_catalog"
REDIS_TTL = 604800  # 7 days — DB is primary store, Redis just a performance cache
CRAWL_INTERVAL = 604800  # crawl weekly, not daily (DB persists skills permanently)


def _guess_category(name: str, description: str) -> str:
    """Return a valid SkillCategory value (uppercase) based on name/description."""
    text = f"{name} {description}".lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                return category
    return "ROUTINE"


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
    """Crawls configured sources for SKILL.md files and caches the catalog.

    Sources = built-in defaults + env ``SKILL_REPOS`` (public GitHub, owner/repo)
    PLUS admin-managed DB rows (``SkillSource``): GitHub repos with a ref/subdir, or
    ANY Git URL cloned shallowly (self-hosted Forgejo/GitLab/Gitea, private repos via
    a masked encrypted credential). Crawled skills pass the same security gate (#192)
    as API-imported ones and carry provenance (source_repo/source_url). Issue #371.
    """

    def __init__(self, redis_service):
        self.redis = redis_service

    async def run(self):
        """Background loop — crawl on startup, then weekly."""
        while True:
            try:
                await self.crawl()
            except Exception as e:
                logger.error("Skill crawler error: %s", e, exc_info=True)
            await asyncio.sleep(CRAWL_INTERVAL)

    async def _load_db_sources(self) -> list[dict]:
        """Load ENABLED admin-configured skill sources (issue #371 phase 3)."""
        try:
            from app.db.session import resilient_session
            from app.models.skill import SkillSource
            from sqlalchemy import select
            async with resilient_session() as db:
                rows = (await db.execute(
                    select(SkillSource).where(SkillSource.enabled.is_(True))
                )).scalars().all()
                return [{
                    "id": r.id, "name": r.name,
                    "kind": r.kind.value if hasattr(r.kind, "value") else str(r.kind),
                    "location": r.location, "ref": r.ref, "subdir": r.subdir,
                    "credential_encrypted": r.credential_encrypted, "trusted": bool(r.trusted),
                } for r in rows]
        except Exception as e:
            logger.warning("Skill crawler: could not load DB sources: %s", e)
            return []

    async def crawl(self) -> list[dict]:
        """Crawl all sources (env/default GitHub + admin DB sources) and cache."""
        github_repos = _configured_repos()
        db_sources = await self._load_db_sources()
        logger.info("Skill crawler: %d built-in/env repos + %d DB sources",
                    len(github_repos), len(db_sources))

        all_skills: list[dict] = []
        from app.config import settings
        headers = {"Accept": "application/vnd.github.v3+json"}
        if settings.github_token:
            headers["Authorization"] = f"Bearer {settings.github_token}"

        async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
            # 1. Built-in + env GitHub repos (public → untrusted provenance).
            for repo in github_repos:
                try:
                    skills = await self._crawl_github(client, repo)
                    for s in skills:
                        s.setdefault("source_repo", repo)
                        s.setdefault("source_url", f"https://github.com/{repo}")
                        s.setdefault("created_by", "import:github")
                        s.setdefault("trusted", False)
                    all_skills.extend(skills)
                except Exception as e:
                    logger.warning("Failed to crawl %s: %s", repo, e)

            # 2. Admin-managed DB sources (GitHub-with-config or any Git host).
            for src in db_sources:
                try:
                    if src["kind"] == "git":
                        skills = await self._crawl_git_source(src)
                        url = src["location"]
                    else:
                        skills = await self._crawl_github(
                            client, src["location"], ref=src.get("ref"), subdir=src.get("subdir"))
                        url = f"https://github.com/{src['location']}"
                    for s in skills:
                        s["source_repo"] = src["location"]
                        s["source_url"] = url
                        s["created_by"] = f"import:source:{src['id']}"
                        s["trusted"] = src["trusted"]
                    all_skills.extend(skills)
                    await self._update_source_status(src["id"], f"ok: {len(skills)} skills")
                except Exception as e:
                    logger.warning("Failed to crawl source '%s' (%s): %s",
                                   src["name"], src["location"], e)
                    await self._update_source_status(src["id"], f"error: {str(e)[:180]}")

        # Deduplicate by name (env/default win over DB sources for name clashes).
        seen: set = set()
        unique: list[dict] = []
        for s in all_skills:
            if s["name"] not in seen:
                seen.add(s["name"])
                unique.append(s)
        unique.sort(key=lambda s: s["name"])

        payload = {
            "skills": unique,
            "crawled_at": datetime.now(timezone.utc).isoformat(),
            "repo_count": len(github_repos) + len(db_sources),
            "skill_count": len(unique),
        }
        if self.redis.client:
            await self.redis.client.set(REDIS_KEY, json.dumps(payload), ex=REDIS_TTL)

        await self._sync_to_db(unique)
        logger.info("Skill crawler: found %d skills", len(unique))
        return unique

    async def _crawl_github(self, client: httpx.AsyncClient, repo: str,
                            ref: str | None = None, subdir: str | None = None) -> list[dict]:
        """Crawl a GitHub repo via the API. Tries the configured ref, then main/master."""
        candidate_refs = [r for r in [ref, "main", "master"] if r]
        tree = None
        used_ref = None
        for r in candidate_refs:
            resp = await client.get(f"https://api.github.com/repos/{repo}/git/trees/{r}?recursive=1")
            if resp.status_code == 200:
                tree = resp.json().get("tree", [])
                used_ref = r
                break
        if tree is None:
            return []
        sub = subdir.strip("/") + "/" if subdir else ""
        skill_paths = [
            it["path"] for it in tree
            if it.get("type") == "blob" and it["path"].endswith("SKILL.md")
            and (not sub or it["path"].startswith(sub))
        ]
        skills = []
        for path in skill_paths:
            try:
                resp = await client.get(f"https://raw.githubusercontent.com/{repo}/{used_ref}/{path}")
                if resp.status_code == 200:
                    s = self._skill_from_content(resp.text, path, repo)
                    if s:
                        skills.append(s)
            except Exception as e:
                logger.debug("fetch %s/%s failed: %s", repo, path, e)
        return skills

    async def _crawl_git_source(self, src: dict) -> list[dict]:
        """Clone ANY Git host shallowly and read SKILL.md files. Host-agnostic; works
        for self-hosted Forgejo/GitLab/Gitea and private repos via a stored credential
        that is injected into the clone URL and NEVER logged."""
        import glob
        import os
        import shutil
        import tempfile
        from app.core.encryption import decrypt_token

        url = src["location"]
        clone_url = url
        cred = src.get("credential_encrypted")
        if cred and url.startswith("https://"):
            try:
                token = decrypt_token(cred)
            except Exception:
                token = ""
            if token:
                clone_url = url.replace("https://", f"https://{token}@", 1)

        tmp = tempfile.mkdtemp(prefix="skillsrc-")
        try:
            cmd = ["git", "clone", "--depth", "1"]
            if src.get("ref"):
                cmd += ["--branch", src["ref"]]
            cmd += [clone_url, tmp]
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
            try:
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            except asyncio.TimeoutError:
                proc.kill()
                raise RuntimeError("git clone timed out")
            if proc.returncode != 0:
                # Scrub the injected credential from any error before it surfaces.
                err = (stderr.decode("utf-8", "replace") if stderr else "").replace(clone_url, url)
                raise RuntimeError(f"git clone failed: {err.strip()[:180]}")
            base = os.path.join(tmp, src["subdir"].strip("/")) if src.get("subdir") else tmp
            skills = []
            for p in glob.glob(os.path.join(base, "**", "SKILL.md"), recursive=True):
                try:
                    with open(p, encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    rel = os.path.relpath(p, tmp)
                    s = self._skill_from_content(content, rel, url)
                    if s:
                        skills.append(s)
                except Exception as e:
                    logger.debug("read %s failed: %s", p, e)
            return skills
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _skill_from_content(self, content: str, path: str, repo: str) -> dict | None:
        """Parse one SKILL.md into a catalog dict (shared by GitHub + Git paths)."""
        frontmatter = _parse_frontmatter(content)
        parts = path.replace("SKILL.md", "").strip("/").split("/")
        name = frontmatter.get("name") or (
            parts[-1] if parts[-1] else (parts[-2] if len(parts) > 1 else repo.split("/")[-1]))
        body = re.sub(r"^---.*?---\s*", "", content, flags=re.DOTALL).strip()
        description = frontmatter.get("description", "")
        if not description:
            first = body.split("\n")[0].strip().lstrip("# ").strip()
            description = first[:120] if first else f"Skill from {repo}"
        return {
            "name": name,
            "description": description,
            "category": _guess_category(name, description),
            "content": body,
        }

    async def _update_source_status(self, source_id: int, status: str) -> None:
        try:
            from app.db.session import resilient_session
            from app.models.skill import SkillSource
            async with resilient_session() as db:
                s = await db.get(SkillSource, source_id)
                if s:
                    s.last_status = status[:250]
                    s.last_crawled_at = datetime.now(timezone.utc)
                    await db.commit()
        except Exception as e:
            logger.debug("update source status failed: %s", e)

    async def _sync_to_db(self, skills: list[dict]) -> None:
        """Upsert crawled skills into the DB marketplace, gated by the security check."""
        try:
            from app.db.session import resilient_session
            from app.models.skill import Skill, SkillStatus, SkillCategory
            from app.core.skill_security import check_skill_content, SkillSecurityError
            from sqlalchemy import select

            async with resilient_session() as db:
                imported = 0
                blocked = 0
                for s in skills:
                    # #192/#371: crawled skills are executable instruction text — gate them
                    # exactly like API-imported skills.
                    try:
                        check_skill_content(s.get("content"))
                    except SkillSecurityError as e:
                        blocked += 1
                        logger.warning("Crawled skill '%s' blocked by security gate: %s",
                                       s.get("name"), getattr(e, "reason", e))
                        continue
                    existing = (await db.execute(
                        select(Skill).where(Skill.name == s["name"])
                    )).scalar_one_or_none()
                    if existing:
                        if s.get("content") and s["content"] != existing.content:
                            existing.content = s["content"]
                            existing.description = s.get("description", existing.description)
                    else:
                        raw_cat = (s.get("category") or "ROUTINE").upper()
                        try:
                            cat = SkillCategory(raw_cat)
                        except ValueError:
                            cat = SkillCategory.ROUTINE
                        db.add(Skill(
                            name=s["name"],
                            description=s.get("description", ""),
                            content=s.get("content", ""),
                            category=cat,
                            status=SkillStatus.ACTIVE,
                            created_by=s.get("created_by", "import:github"),
                            source_repo=s.get("source_repo"),
                            source_url=s.get("source_url"),
                        ))
                        imported += 1
                await db.commit()
                if imported or blocked:
                    logger.info("Skill crawler: imported %d new, %d blocked by security gate",
                                imported, blocked)
        except Exception as e:
            logger.warning("Skill crawler: DB sync failed: %s", e)

    async def get_catalog(self) -> dict | None:
        if not self.redis.client:
            return None
        data = await self.redis.client.get(REDIS_KEY)
        if data:
            return json.loads(data)
        return None
