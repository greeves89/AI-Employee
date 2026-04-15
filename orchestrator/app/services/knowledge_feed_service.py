"""Knowledge Feed Service — the worker that runs all feeds.

This module implements the 4 feed types for issues #41, #42, #43, #44:

   feed_type              issue   what it does
   -------------------    -----   -------------------------------------
   mcp_registry           #41     scrape the MCP catalog on GitHub for
                                  new servers, dedupe by repo URL
   ai_news                #42     pull Anthropic/OpenAI/Google blog RSS,
                                  stash titles + summaries
   competitor             #43     poll OpenClaw / Devin / Cursor release
                                  pages, track diffs
   best_practices         #44     fetch well-known "Claude Code best
                                  practices" sources to feed the auto-
                                  CLAUDE.md updater

Every feed is fetched via a small plugin function that returns a list of
raw items. The shared code dedupes them against the DB and commits.
New items are published on Redis channel `knowledge_feed:new` so other
services (agents, UI) can react.

All HTTP calls use httpx with a 30s timeout. Network errors are caught
per-feed and recorded in `last_error` without crashing the whole run.

CRITICAL: plugin functions must NEVER write to the DB directly — they
only return dicts. The service handles persistence + dedup in one place.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import async_session_factory
from app.models.knowledge_feed import KnowledgeFeed, KnowledgeFeedItem
from app.services.redis_service import RedisService

logger = logging.getLogger(__name__)

HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
USER_AGENT = "AI-Employee-KnowledgeFeed/1.0"


# ---------------------------------------------------------------------------
# Plugin type and registry
# ---------------------------------------------------------------------------
#
# A plugin takes the feed config dict and returns a list of
#   {"external_id", "title", "url"?, "summary"?, "content"?, "metadata"?,
#    "published_at"?}
# raw items. external_id is what gets deduped.

FeedPlugin = Callable[[dict[str, Any]], Awaitable[list[dict[str, Any]]]]

FEED_PLUGINS: dict[str, FeedPlugin] = {}


def register(feed_type: str):
    def deco(fn: FeedPlugin) -> FeedPlugin:
        FEED_PLUGINS[feed_type] = fn
        return fn
    return deco


# ---------------------------------------------------------------------------
# #41 — MCP Server Auto-Discovery
# ---------------------------------------------------------------------------

MCP_CATALOG_API = "https://api.github.com/repos/modelcontextprotocol/servers/contents/src"


@register("mcp_registry")
async def fetch_mcp_registry(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Fetch list of MCP servers from modelcontextprotocol/servers.

    Returns one item per server directory. external_id = GitHub path.
    """
    source = config.get("source_url") or MCP_CATALOG_API
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT}) as client:
        resp = await client.get(source)
        resp.raise_for_status()
        entries = resp.json()

    if not isinstance(entries, list):
        return []

    items: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("type") != "dir":
            continue
        name = entry.get("name") or ""
        path = entry.get("path") or ""
        html_url = entry.get("html_url") or ""
        items.append(
            {
                "external_id": path or name,
                "title": f"MCP server: {name}",
                "url": html_url,
                "summary": f"Official MCP server '{name}' from modelcontextprotocol/servers.",
                "metadata": {
                    "server_name": name,
                    "install_command": f"npx -y @modelcontextprotocol/server-{name}",
                },
            }
        )
    return items


# ---------------------------------------------------------------------------
# #42 — AI News Knowledge Base
# ---------------------------------------------------------------------------

DEFAULT_AI_NEWS_SOURCES = [
    "https://www.anthropic.com/news/feed.xml",
    "https://openai.com/blog/rss.xml",
    "https://deepmind.google/discover/blog/rss.xml",
]


@register("ai_news")
async def fetch_ai_news(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Very small RSS reader — fetches configured feeds and extracts
    <item>…<title>…<link>…<description> tuples via regex (no extra deps).
    """
    sources: list[str] = config.get("sources") or DEFAULT_AI_NEWS_SOURCES
    items: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT}) as client:
        for source in sources:
            try:
                resp = await client.get(source)
                if resp.status_code != 200:
                    continue
                xml = resp.text
            except Exception as e:
                logger.warning(f"[KnowledgeFeed] ai_news fetch failed for {source}: {e}")
                continue

            # Crude RSS parsing — matches both <item> (RSS) and <entry> (Atom)
            item_blocks = re.findall(r"<(?:item|entry)\b[^>]*>(.*?)</(?:item|entry)>", xml, re.DOTALL)
            for block in item_blocks[:20]:  # cap per-source
                title_m = re.search(r"<title[^>]*>(.*?)</title>", block, re.DOTALL)
                link_m = re.search(r"<link[^>]*>(.*?)</link>", block, re.DOTALL) or re.search(
                    r'<link[^>]*href="([^"]+)"', block
                )
                desc_m = re.search(r"<(?:description|summary)[^>]*>(.*?)</(?:description|summary)>", block, re.DOTALL)
                if not title_m:
                    continue
                title = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", title_m.group(1)).strip()
                link = link_m.group(1).strip() if link_m else ""
                summary = re.sub(r"<[^>]+>", "", desc_m.group(1)).strip() if desc_m else ""
                items.append(
                    {
                        "external_id": link or title,
                        "title": title[:500],
                        "url": link,
                        "summary": summary[:2000],
                        "metadata": {"source": source},
                    }
                )
    return items


# ---------------------------------------------------------------------------
# #43 — Competitor Feature Tracker
# ---------------------------------------------------------------------------

DEFAULT_COMPETITOR_SOURCES = {
    "cursor": "https://api.github.com/repos/getcursor/cursor/releases",
    "windsurf": "https://api.github.com/repos/windsurf-ai/windsurf/releases",
    "devin": "https://cognition.ai/blog/feed.xml",
}


@register("competitor")
async def fetch_competitor_releases(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Poll GitHub releases for competitor tools. Each release = one item."""
    sources: dict[str, str] = config.get("sources") or DEFAULT_COMPETITOR_SOURCES
    items: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT}) as client:
        for competitor, url in sources.items():
            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    continue
                data = resp.json()
            except Exception as e:
                logger.warning(f"[KnowledgeFeed] competitor fetch failed for {competitor}: {e}")
                continue

            if not isinstance(data, list):
                continue
            for release in data[:10]:
                if not isinstance(release, dict):
                    continue
                tag = release.get("tag_name") or release.get("name") or ""
                body = release.get("body") or ""
                published = release.get("published_at")
                items.append(
                    {
                        "external_id": f"{competitor}:{tag}",
                        "title": f"{competitor} {tag}",
                        "url": release.get("html_url") or "",
                        "summary": (body[:500] + "…") if len(body) > 500 else body,
                        "content": body[:20000],
                        "published_at": _parse_iso(published),
                        "metadata": {"competitor": competitor, "tag": tag},
                    }
                )
    return items


# ---------------------------------------------------------------------------
# #44 — Trend-Driven CLAUDE.md Auto-Update
# ---------------------------------------------------------------------------

DEFAULT_BEST_PRACTICES_SOURCES = [
    "https://api.github.com/search/repositories?q=claude+code+best+practices&sort=updated&per_page=10",
    "https://api.github.com/search/repositories?q=claude+code+skills&sort=updated&per_page=10",
]


@register("best_practices")
async def fetch_best_practices(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Find recently-updated repos whose README describes Claude Code
    best practices. Feeds the CLAUDE.md auto-updater with candidate
    snippets to review.
    """
    sources: list[str] = config.get("sources") or DEFAULT_BEST_PRACTICES_SOURCES
    items: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT}) as client:
        for source in sources:
            try:
                resp = await client.get(source)
                if resp.status_code != 200:
                    continue
                data = resp.json()
            except Exception as e:
                logger.warning(f"[KnowledgeFeed] best_practices fetch failed for {source}: {e}")
                continue

            hits = data.get("items") if isinstance(data, dict) else []
            for repo in hits[:10] if isinstance(hits, list) else []:
                if not isinstance(repo, dict):
                    continue
                full_name = repo.get("full_name") or ""
                items.append(
                    {
                        "external_id": f"github:{full_name}",
                        "title": full_name,
                        "url": repo.get("html_url") or "",
                        "summary": repo.get("description") or "",
                        "metadata": {
                            "stars": repo.get("stargazers_count", 0),
                            "updated_at": repo.get("updated_at"),
                            "language": repo.get("language"),
                        },
                    }
                )
    return items


# ---------------------------------------------------------------------------
# Runner — used by scheduler
# ---------------------------------------------------------------------------


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


async def _run_single_feed(session: AsyncSession, feed: KnowledgeFeed) -> tuple[int, str | None]:
    """Execute one feed. Returns (new_items_count, error_message_or_None)."""
    plugin = FEED_PLUGINS.get(feed.feed_type)
    if not plugin:
        return 0, f"unknown feed type: {feed.feed_type}"

    try:
        raw_items = await plugin(dict(feed.config or {}))
    except Exception as e:
        return 0, f"plugin raised: {type(e).__name__}: {e}"

    new_count = 0
    now = datetime.now(timezone.utc)

    for raw in raw_items:
        external_id = str(raw.get("external_id") or "")[:500]
        if not external_id:
            continue

        stmt = pg_insert(KnowledgeFeedItem).values(
            feed_id=feed.id,
            external_id=external_id,
            title=str(raw.get("title") or "")[:500],
            url=raw.get("url"),
            summary=raw.get("summary"),
            content=raw.get("content"),
            meta_json=raw.get("metadata") or {},
            published_at=raw.get("published_at") if isinstance(raw.get("published_at"), datetime) else None,
            harvested_at=now,
            seen=False,
        ).on_conflict_do_nothing(index_elements=["feed_id", "external_id"])
        result = await session.execute(stmt)
        if result.rowcount and result.rowcount > 0:
            new_count += 1

    feed.last_run_at = now
    feed.last_status = "ok"
    feed.last_error = None
    feed.items_count = (feed.items_count or 0) + new_count
    await session.commit()
    return new_count, None


class KnowledgeFeedService:
    """Called by the scheduler every minute. Picks feeds that are
    overdue (last_run_at + interval_minutes < now) and runs them.
    """

    def __init__(self, redis: RedisService) -> None:
        self.redis = redis

    async def tick(self) -> dict[str, Any]:
        """Run all overdue feeds. Safe to call frequently."""
        summary = {"ran": 0, "errors": 0, "new_items": 0}
        now = datetime.now(timezone.utc)

        async with async_session_factory() as session:
            result = await session.execute(
                select(KnowledgeFeed).where(KnowledgeFeed.enabled.is_(True))
            )
            feeds = list(result.scalars().all())

            for feed in feeds:
                overdue = (
                    feed.last_run_at is None
                    or feed.last_run_at + timedelta(minutes=feed.interval_minutes) < now
                )
                if not overdue:
                    continue

                new_count, err = await _run_single_feed(session, feed)
                summary["ran"] += 1
                summary["new_items"] += new_count
                if err:
                    summary["errors"] += 1
                    feed.last_run_at = now
                    feed.last_status = "error"
                    feed.last_error = err[:2000]
                    await session.commit()
                    logger.warning(f"[KnowledgeFeed] feed {feed.id} ({feed.feed_type}) failed: {err}")
                else:
                    logger.info(
                        f"[KnowledgeFeed] feed {feed.id} ({feed.feed_type}) ok — {new_count} new items"
                    )
                    if new_count > 0:
                        try:
                            await self.redis.client.publish(
                                "knowledge_feed:new",
                                json.dumps(
                                    {"feed_id": feed.id, "feed_type": feed.feed_type, "count": new_count}
                                ),
                            )
                        except Exception:
                            pass  # don't crash on pub/sub error

        return summary

    async def run_now(self, feed_id: int) -> dict[str, Any]:
        """Manually trigger one feed (used by the API endpoint)."""
        async with async_session_factory() as session:
            feed = await session.get(KnowledgeFeed, feed_id)
            if not feed:
                raise ValueError(f"feed {feed_id} not found")
            new_count, err = await _run_single_feed(session, feed)
            if err:
                feed.last_status = "error"
                feed.last_error = err[:2000]
                feed.last_run_at = datetime.now(timezone.utc)
                await session.commit()
                return {"status": "error", "error": err, "new_items": 0}
            return {"status": "ok", "new_items": new_count}
