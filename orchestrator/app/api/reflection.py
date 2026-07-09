"""Reflection ("Nachtschicht") API — run log, status and manual trigger.

Powers the dashboard card, the settings card and the approvals tab. All
endpoints are user-facing (require_auth); the run itself is orchestrator-
internal (scheduler) or triggered manually here.
"""

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import require_auth
from app.models.command_approval import ApprovalStatus, CommandApproval
from app.models.reflection_run import ReflectionRun
from app.models.user import UserRole

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reflection", tags=["reflection"])

# Module-level guard: only one manual run at a time per process.
_manual_run_lock = asyncio.Lock()


def _run_to_dict(r: ReflectionRun) -> dict:
    return {
        "id": r.id,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "status": r.status,
        "mode": r.mode,
        "trigger": r.trigger,
        "stats": r.stats or {},
        "tokens_used": r.tokens_used,
        "cost_usd": r.cost_usd,
        "error": r.error,
    }


@router.get("/status")
async def reflection_status(user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Last run + pending approval count — the dashboard card in one call."""
    from app.services.reflection_service import ReflectionService
    cfg = await ReflectionService()._load_config(db)
    last = (await db.execute(
        select(ReflectionRun).order_by(ReflectionRun.started_at.desc()).limit(1)
    )).scalar_one_or_none()
    pending = (await db.execute(
        select(func.count(CommandApproval.id)).where(and_(
            CommandApproval.command == "reflection_change",
            CommandApproval.status == ApprovalStatus.PENDING,
        ))
    )).scalar() or 0
    return {
        "enabled": cfg["enabled"],
        "mode": cfg["mode"],
        "hour": cfg["hour"],
        "token_budget": cfg["token_budget"],
        "pending_approvals": int(pending),
        "last_run": _run_to_dict(last) if last else None,
    }


@router.get("/runs")
async def list_runs(
    limit: int = 20,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(
        select(ReflectionRun).order_by(ReflectionRun.started_at.desc()).limit(min(limit, 100))
    )).scalars().all()
    return {"runs": [_run_to_dict(r) for r in rows]}


@router.post("/run-now")
async def run_now(user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Manually trigger a reflection run (admin only). Runs in the background."""
    if getattr(user, "role", None) != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin only")
    if _manual_run_lock.locked():
        raise HTTPException(status_code=409, detail="Ein Lauf ist bereits aktiv")

    from app.services.reflection_service import ReflectionService

    async def _run():
        async with _manual_run_lock:
            try:
                await ReflectionService().run(trigger="manual")
            except Exception:  # noqa: BLE001
                logger.exception("[Reflection] manual run failed")

    asyncio.create_task(_run())
    return {"started": True, "at": datetime.now(timezone.utc).isoformat()}
