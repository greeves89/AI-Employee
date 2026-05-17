"""Vertical packs API — list, preview and provision industry starter kits (#159)."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.vertical_packs import BUILTIN_VERTICAL_PACKS, get_pack
from app.db.session import get_db
from app.dependencies import get_docker_service, get_redis_service, require_auth
from app.models.agent_template import AgentTemplate
from app.services.docker_service import DockerService
from app.services.redis_service import RedisService
from app.services.vertical_pack_provisioner import provision_pack

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/vertical-packs", tags=["vertical-packs"])


def _pack_summary(pack: dict) -> dict:
    return {
        "slug": pack["slug"],
        "name": pack["name"],
        "description": pack["description"],
        "icon": pack["icon"],
        "industry": pack["industry"],
        "agent_count": len(pack.get("template_names", [])),
    }


async def _resolve_templates(db: AsyncSession, names: list[str]) -> list[dict]:
    """Resolve template names to display info for the preview."""
    if not names:
        return []
    rows = (await db.execute(
        select(AgentTemplate).where(AgentTemplate.name.in_(names))
    )).scalars().all()
    by_name = {t.name: t for t in rows}
    out = []
    for name in names:
        t = by_name.get(name)
        out.append({
            "name": name,
            "display_name": t.display_name if t else name,
            "description": t.description if t else "",
            "available": t is not None,
        })
    return out


@router.get("")
async def list_vertical_packs(user=Depends(require_auth)):
    """List all available vertical packs."""
    return {"packs": [_pack_summary(p) for p in BUILTIN_VERTICAL_PACKS]}


@router.get("/{slug}")
async def get_vertical_pack(
    slug: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Preview a vertical pack — agents, knowledge and demo task it will create."""
    pack = get_pack(slug)
    if not pack:
        raise HTTPException(status_code=404, detail="Vertical pack not found")
    return {
        **_pack_summary(pack),
        "agents": await _resolve_templates(db, pack.get("template_names", [])),
        "knowledge_entries": [
            {"title": e["title"], "tags": e.get("tags", [])}
            for e in pack.get("knowledge_entries", [])
        ],
        "demo_task": pack.get("demo_task"),
    }


@router.post("/{slug}/provision")
async def provision_vertical_pack(
    slug: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    docker: DockerService = Depends(get_docker_service),
    redis: RedisService = Depends(get_redis_service),
):
    """Provision a vertical pack for the current user — creates agents, knowledge, demo task."""
    pack = get_pack(slug)
    if not pack:
        raise HTTPException(status_code=404, detail="Vertical pack not found")

    uid = user.id if getattr(user, "id", None) and user.id != "__anonymous__" else None
    try:
        result = await provision_pack(pack, uid, db, docker, redis)
    except Exception as e:
        logger.error(f"[VerticalPack] Provisioning '{slug}' failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Provisioning failed: {e}")

    return {
        "status": "completed",
        "message": f"Paket '{pack['name']}' eingerichtet — {len(result['agents'])} Agenten erstellt.",
        **result,
    }
