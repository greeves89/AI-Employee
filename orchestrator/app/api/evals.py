"""Golden-Tests: Sammlungen pflegen, Läufe starten, Verlauf ansehen (#391).

Ein Prompt-, Modell- oder Skill-Update kann eine Agentenrolle heimlich
verschlechtern. Man merkt es Wochen später an einem falschen Bericht — und weiss
dann nicht mehr, welche Änderung es war. Diese Sammlungen sind der Regressionstest
dafür, und das Gatter beim Update ist die Stelle, an der er zählt.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import eval_harness
from app.core.load_balancer import LoadBalancer
from app.core.log_redaction import scrub_log
from app.core.task_router import TaskRouter
from app.db.session import get_db
from app.dependencies import get_redis_service, require_auth
from app.models.agent import Agent
from app.models.eval_set import EvalRun, EvalSet
from app.services import eval_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/evals", tags=["evals"])


class EvalSetIn(BaseModel):
    name: str
    role: str = ""
    description: str = ""
    items: list


class RunIn(BaseModel):
    agent_id: str


def _set_to_dict(s: EvalSet) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "role": s.role,
        "description": s.description,
        "version": s.version,
        "items": s.items or [],
        "item_count": len(s.items or []),
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


def _run_to_dict(r: EvalRun) -> dict:
    return {
        "id": r.id,
        "set_id": r.set_id,
        "set_version": r.set_version,
        "agent_id": r.agent_id,
        "status": r.status,
        "score": r.score,
        "passed": r.passed,
        "total": r.total,
        "baseline_score": r.baseline_score,
        "regression": r.regression,
        "trigger": r.trigger,
        "results": r.results or [],
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "completed_at": r.completed_at.isoformat() if r.completed_at else None,
    }


async def _owned_agent(db: AsyncSession, agent_id: str, user) -> Agent:
    """Nur eigene Agenten. Ohne diese Pruefung koennte jeder gegen fremde Agenten
    Testlaeufe starten — und damit deren Warteschlange und Kosten belasten."""
    agent = (await db.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent nicht gefunden")
    if agent.user_id is not None and str(agent.user_id) != str(user.id) and user.role != "admin":
        raise HTTPException(status_code=403, detail="Kein Zugriff auf diesen Agenten")
    return agent


async def _owned_set(db: AsyncSession, set_id: str, user) -> EvalSet:
    row = (await db.execute(select(EvalSet).where(EvalSet.id == set_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Sammlung nicht gefunden")
    if row.user_id is not None and str(row.user_id) != str(user.id) and user.role != "admin":
        raise HTTPException(status_code=403, detail="Kein Zugriff auf diese Sammlung")
    return row


@router.get("/sets")
async def list_sets(
    role: str | None = Query(None),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    query = select(EvalSet).order_by(EvalSet.updated_at.desc())
    rows = (await db.execute(query)).scalars().all()
    rows = [r for r in rows if r.user_id is None or str(r.user_id) == str(user.id) or user.role == "admin"]
    if role:
        rows = [r for r in rows if r.role == role]
    return {"sets": [_set_to_dict(r) for r in rows]}


@router.post("/sets")
async def create_set(
    body: EvalSetIn,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Eine Sammlung anlegen. Ungültige Aufgaben werden abgelehnt, nicht gespeichert.

    Eine Sammlung, die halb stimmt, ist schlimmer als keine: sie liefert einen
    Wert, dem man glaubt.
    """
    try:
        items = eval_harness.validate_items(body.items)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    row = EvalSet(
        id=eval_service.new_id("es"),
        name=body.name.strip()[:200] or "Golden-Tests",
        role=body.role.strip()[:100],
        description=body.description.strip()[:2000],
        items=items,
        version=1,
        user_id=str(user.id),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _set_to_dict(row)


@router.put("/sets/{set_id}")
async def update_set(
    set_id: str,
    body: EvalSetIn,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Eine Sammlung ändern. Ändern sich die Aufgaben, steigt die Fassung.

    Ohne das wäre ein Vergleich zwischen zwei Läufen wertlos: ein besserer Wert
    könnte auch nur eine leichtere Aufgabe bedeuten.
    """
    row = await _owned_set(db, set_id, user)
    try:
        items = eval_harness.validate_items(body.items)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if items != (row.items or []):
        row.version = (row.version or 1) + 1
        row.items = items
    row.name = body.name.strip()[:200] or row.name
    row.role = body.role.strip()[:100]
    row.description = body.description.strip()[:2000]
    await db.commit()
    await db.refresh(row)
    return _set_to_dict(row)


@router.delete("/sets/{set_id}")
async def delete_set(
    set_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    row = await _owned_set(db, set_id, user)
    await db.delete(row)
    await db.commit()
    return {"status": "deleted", "id": set_id}


@router.post("/sets/{set_id}/run")
async def run_set(
    set_id: str,
    body: RunIn,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis_service),
):
    """Die Sammlung gegen einen Agenten laufen lassen.

    Kehrt sofort zurück: die Aufgaben laufen als gewöhnliche Aufträge durch die
    Warteschlange, und die Bewertung hängt am normalen Abschluss. Ein Testlauf mit
    zehn Aufgaben dauert Minuten — darauf zu warten wäre eine Anfrage, die in jede
    Zeitgrenze läuft.
    """
    eval_set = await _owned_set(db, set_id, user)
    agent = await _owned_agent(db, body.agent_id, user)

    task_router = TaskRouter(db, redis, LoadBalancer(redis))
    try:
        run = await eval_service.start_run(db, task_router, eval_set, agent.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.exception("[Eval] Lauf konnte nicht starten")
        raise HTTPException(status_code=500, detail=f"Lauf fehlgeschlagen: {scrub_log(str(e))}")
    return _run_to_dict(run)


@router.get("/runs")
async def list_runs(
    agent_id: str | None = Query(None),
    set_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Der Verlauf — die Zahlenreihe, an der man einen Rückschritt überhaupt sieht."""
    query = select(EvalRun).order_by(EvalRun.created_at.desc()).limit(limit)
    if agent_id:
        await _owned_agent(db, agent_id, user)
        query = query.where(EvalRun.agent_id == agent_id)
    if set_id:
        query = query.where(EvalRun.set_id == set_id)
    rows = (await db.execute(query)).scalars().all()
    return {"runs": [_run_to_dict(r) for r in rows]}


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    row = (await db.execute(select(EvalRun).where(EvalRun.id == run_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Lauf nicht gefunden")
    await _owned_agent(db, row.agent_id, user)
    return _run_to_dict(row)


@router.get("/gate/{agent_id}")
async def check_gate(
    agent_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Würde ein Update jetzt durchgelassen? — dieselbe Antwort wie beim Update.

    Damit man es NACHSEHEN kann, statt es am blockierten Update zu merken.
    """
    agent = await _owned_agent(db, agent_id, user)
    return await eval_service.gate_for_agent(db, agent)


class GateSettings(BaseModel):
    enabled: bool | None = None
    require_run: bool | None = None
    tolerance: float | None = None


@router.patch("/gate/{agent_id}")
async def update_gate(
    agent_id: str,
    body: GateSettings,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Das Gatter einstellen.

    ``require_run`` ist die schärfere Haltung: ohne Lauf kein Update. Standardmässig
    aus — ein Gatter, das jedes Update blockiert, wird binnen einer Woche
    abgeschaltet und schützt dann gar nichts mehr.
    """
    agent = await _owned_agent(db, agent_id, user)
    config = dict(agent.config or {})
    gate = dict(config.get(eval_service.GATE_KEY) or {})
    if body.enabled is not None:
        gate["enabled"] = bool(body.enabled)
    if body.require_run is not None:
        gate["require_run"] = bool(body.require_run)
    if body.tolerance is not None:
        if not 0 <= body.tolerance <= 100:
            raise HTTPException(status_code=400, detail="Toleranz liegt zwischen 0 und 100")
        gate["tolerance"] = float(body.tolerance)
    config[eval_service.GATE_KEY] = gate
    agent.config = config
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(agent, "config")
    await db.commit()
    return {"agent_id": agent_id, "gate": gate}
