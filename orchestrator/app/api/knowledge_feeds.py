"""API for knowledge feeds — addresses #41 #42 #43 #44.

Endpoints:
  GET    /api/v1/knowledge-feeds/                 list all feeds
  POST   /api/v1/knowledge-feeds/                 create a feed
  GET    /api/v1/knowledge-feeds/{id}             feed details
  PATCH  /api/v1/knowledge-feeds/{id}             enable/disable/update
  DELETE /api/v1/knowledge-feeds/{id}             remove feed
  POST   /api/v1/knowledge-feeds/{id}/run         trigger feed manually
  GET    /api/v1/knowledge-feeds/{id}/items       list harvested items
  POST   /api/v1/knowledge-feeds/seed-defaults    one-click install 4 default feeds
"""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import get_redis_service, require_auth, require_manager
from app.models.knowledge_feed import KnowledgeFeed, KnowledgeFeedItem
from app.services.knowledge_feed_service import FEED_PLUGINS, KnowledgeFeedService
from app.services.redis_service import RedisService

router = APIRouter(prefix="/knowledge-feeds", tags=["knowledge-feeds"])


class FeedCreate(BaseModel):
    feed_type: str = Field(..., description="One of: mcp_registry, ai_news, competitor, best_practices")
    name: str = Field(..., max_length=200)
    source_url: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    interval_minutes: int = Field(default=1440, ge=5, le=60 * 24 * 14)
    enabled: bool = True


class FeedPatch(BaseModel):
    name: str | None = None
    source_url: str | None = None
    config: dict[str, Any] | None = None
    interval_minutes: int | None = Field(default=None, ge=5, le=60 * 24 * 14)
    enabled: bool | None = None


class FeedResponse(BaseModel):
    id: int
    feed_type: str
    name: str
    source_url: str | None
    config: dict[str, Any]
    interval_minutes: int
    enabled: bool
    last_run_at: datetime | None
    last_status: str | None
    last_error: str | None
    items_count: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class FeedItemResponse(BaseModel):
    id: int
    feed_id: int
    external_id: str
    title: str
    url: str | None
    summary: str | None
    metadata: dict[str, Any] = Field(alias="meta_json")
    published_at: datetime | None
    harvested_at: datetime
    seen: bool

    class Config:
        from_attributes = True
        populate_by_name = True


class FeedRunResponse(BaseModel):
    status: str
    new_items: int
    error: str | None = None


@router.get("/", response_model=list[FeedResponse])
async def list_feeds(
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(KnowledgeFeed).order_by(KnowledgeFeed.id))
    feeds = list(result.scalars().all())
    return [FeedResponse.model_validate(f) for f in feeds]


@router.post("/", response_model=FeedResponse, status_code=201)
async def create_feed(
    data: FeedCreate,
    user=Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    if data.feed_type not in FEED_PLUGINS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown feed_type '{data.feed_type}'. Known: {list(FEED_PLUGINS.keys())}",
        )
    feed = KnowledgeFeed(
        feed_type=data.feed_type,
        name=data.name,
        source_url=data.source_url,
        config=data.config,
        interval_minutes=data.interval_minutes,
        enabled=data.enabled,
    )
    db.add(feed)
    await db.commit()
    await db.refresh(feed)
    return FeedResponse.model_validate(feed)


@router.get("/{feed_id}", response_model=FeedResponse)
async def get_feed(
    feed_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    feed = await db.get(KnowledgeFeed, feed_id)
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")
    return FeedResponse.model_validate(feed)


@router.patch("/{feed_id}", response_model=FeedResponse)
async def update_feed(
    feed_id: int,
    data: FeedPatch,
    user=Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    feed = await db.get(KnowledgeFeed, feed_id)
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")

    for field in ("name", "source_url", "config", "interval_minutes", "enabled"):
        val = getattr(data, field)
        if val is not None:
            setattr(feed, field, val)
    await db.commit()
    await db.refresh(feed)
    return FeedResponse.model_validate(feed)


@router.delete("/{feed_id}")
async def delete_feed(
    feed_id: int,
    user=Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    feed = await db.get(KnowledgeFeed, feed_id)
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")
    await db.delete(feed)
    await db.commit()
    return {"status": "deleted", "id": feed_id}


@router.post("/{feed_id}/run", response_model=FeedRunResponse)
async def run_feed_now(
    feed_id: int,
    user=Depends(require_manager),
    redis: RedisService = Depends(get_redis_service),
):
    service = KnowledgeFeedService(redis)
    try:
        result = await service.run_now(feed_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return FeedRunResponse(**result)


@router.get("/{feed_id}/items", response_model=list[FeedItemResponse])
async def list_feed_items(
    feed_id: int,
    limit: int = Query(default=50, ge=1, le=500),
    only_unseen: bool = Query(default=False),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(KnowledgeFeedItem)
        .where(KnowledgeFeedItem.feed_id == feed_id)
        .order_by(desc(KnowledgeFeedItem.harvested_at))
        .limit(limit)
    )
    if only_unseen:
        stmt = stmt.where(KnowledgeFeedItem.seen.is_(False))
    result = await db.execute(stmt)
    items = list(result.scalars().all())
    return [FeedItemResponse.model_validate(i) for i in items]


@router.post("/seed-defaults", status_code=201)
async def seed_default_feeds(
    user=Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """One-click install of the 4 default feeds (one per issue).
    Idempotent — re-running will not duplicate existing feeds.
    """
    defaults = [
        {
            "feed_type": "mcp_registry",
            "name": "MCP Official Servers",
            "source_url": "https://api.github.com/repos/modelcontextprotocol/servers/contents/src",
            "interval_minutes": 1440,
        },
        {
            "feed_type": "ai_news",
            "name": "AI News (Anthropic + OpenAI + DeepMind)",
            "source_url": None,
            "interval_minutes": 360,
        },
        {
            "feed_type": "competitor",
            "name": "Competitor Releases (Cursor + Windsurf + Devin)",
            "source_url": None,
            "interval_minutes": 1440,
        },
        {
            "feed_type": "best_practices",
            "name": "Claude Code Best Practices",
            "source_url": None,
            "interval_minutes": 1440 * 7,  # weekly
        },
    ]
    created: list[FeedResponse] = []
    for spec in defaults:
        existing = await db.scalar(
            select(KnowledgeFeed).where(
                KnowledgeFeed.feed_type == spec["feed_type"],
                KnowledgeFeed.name == spec["name"],
            )
        )
        if existing:
            created.append(FeedResponse.model_validate(existing))
            continue
        feed = KnowledgeFeed(
            feed_type=spec["feed_type"],
            name=spec["name"],
            source_url=spec.get("source_url"),
            config={},
            interval_minutes=spec["interval_minutes"],
            enabled=True,
        )
        db.add(feed)
        await db.commit()
        await db.refresh(feed)
        created.append(FeedResponse.model_validate(feed))

    return {"feeds": [f.model_dump() for f in created], "count": len(created)}
