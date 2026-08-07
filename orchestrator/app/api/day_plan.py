"""Tagesplan eines Agenten — schreiben tut ihn der Agent, sehen und korrigieren darfst du.

Der Agent ruft ``PUT /agents/{id}/day-plan`` am Anfang seines Laufs (PROACTIVE_PROMPT
STEP 1) und legt damit den Plan fuer den Tag ab. Die Oberflaeche liest ihn ueber
``GET`` und zeigt ihn im Kalender neben den erledigten Aufgaben; ueber ``PATCH`` kannst
du einen Block verschieben oder streichen, und der naechste Lauf sieht das.

Ein Plan-Eintrag ist NICHT dasselbe wie ein Todo: das Todo ist die Arbeit, der Eintrag
ist der Zeitpunkt, zu dem sie eingeplant ist.
"""

import logging
from datetime import date as date_cls, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import require_auth, require_auth_or_agent
from app.models.agent_plan_item import AgentPlanItem

logger = logging.getLogger(__name__)
router = APIRouter(tags=["day-plan"])

# Ein Tag hat keine 60 sinnvollen Bloecke — die Grenze haelt einen ausufernden Plan
# (und die Kalenderansicht) im Rahmen.
MAX_PLAN_ITEMS = 40
VALID_SOURCES = ("responsibility", "todo", "self", "user")
VALID_STATUS = ("planned", "running", "done", "dropped")


# Reihenfolge bei gleicher (oder fehlender) Uhrzeit: hoch vor normal vor niedrig.
# Ohne das erbte der Plan keine der bestehenden Prioritaeten und war reine Chronologie.
_PRIORITY_RANK = {"high": 0, "normal": 1, "low": 2}


class PlanItemIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    notes: str = ""
    planned_start: datetime | None = None
    estimated_minutes: int = Field(default=30, ge=1, le=1440)
    source: str = "self"
    priority: str = "normal"   # high | normal | low — vom Bereich oder vom Todo geerbt
    todo_id: int | None = None


class DayPlanIn(BaseModel):
    plan_date: date_cls | None = None  # Standard: heute (UTC)
    items: list[PlanItemIn] = []


class PlanItemPatch(BaseModel):
    planned_start: datetime | None = None
    estimated_minutes: int | None = Field(default=None, ge=1, le=1440)
    status: str | None = None
    title: str | None = None
    notes: str | None = None


def _to_response(item: AgentPlanItem) -> dict:
    return {
        "id": item.id,
        "agent_id": item.agent_id,
        "plan_date": item.plan_date.isoformat() if item.plan_date else None,
        "title": item.title,
        "notes": item.notes or "",
        "planned_start": item.planned_start.isoformat() if item.planned_start else None,
        "estimated_minutes": item.estimated_minutes,
        "source": item.source,
        "priority": item.priority,
        "status": item.status,
        "todo_id": item.todo_id,
        "task_id": item.task_id,
    }


async def _assert_access(agent_id: str, user, db: AsyncSession) -> None:
    """Ein Agent darf nur seinen EIGENEN Plan anfassen; ein Nutzer nur die Agenten,
    die er sehen darf. Ohne diese Pruefung koennte ein Agent-Token den Plan eines
    fremden Agenten ueberschreiben."""
    from app.core.ownership import is_admin, visible_agent_ids
    from app.dependencies import is_agent_principal

    if is_agent_principal(user):
        if user.id != agent_id:
            raise HTTPException(status_code=403, detail="Agent can only touch its own day plan")
        return
    if is_admin(user):
        return
    vids = await visible_agent_ids(user, db)
    if vids is not None and agent_id not in vids:
        raise HTTPException(status_code=403, detail="Access denied")


@router.put("/agents/{agent_id}/day-plan")
async def replace_day_plan(
    agent_id: str,
    body: DayPlanIn,
    user=Depends(require_auth_or_agent),
    db: AsyncSession = Depends(get_db),
):
    """Plan fuer einen Tag setzen (ersetzt den bestehenden Plan dieses Tages).

    Ersetzen statt Anhaengen, weil ein Lauf seinen Plan neu fasst — sonst wuerde sich
    jeder proaktive Durchgang seine eigenen Reste erneut in den Kalender legen.
    ERLEDIGTE und LAUFENDE Eintraege bleiben allerdings stehen: was der Tag schon
    gesehen hat, darf eine Neuplanung nicht aus der Geschichte loeschen.
    """
    await _assert_access(agent_id, user, db)
    if len(body.items) > MAX_PLAN_ITEMS:
        raise HTTPException(status_code=422, detail=f"Höchstens {MAX_PLAN_ITEMS} Blöcke pro Tag")

    plan_date = body.plan_date or datetime.now(timezone.utc).date()

    await db.execute(
        delete(AgentPlanItem).where(
            AgentPlanItem.agent_id == agent_id,
            AgentPlanItem.plan_date == plan_date,
            AgentPlanItem.status.in_(("planned", "dropped")),
        )
    )
    created = []
    for item in body.items:
        source = item.source if item.source in VALID_SOURCES else "self"
        row = AgentPlanItem(
            agent_id=agent_id,
            plan_date=plan_date,
            title=item.title.strip(),
            notes=(item.notes or "").strip()[:2000],
            planned_start=item.planned_start,
            estimated_minutes=item.estimated_minutes,
            source=source,
            priority=item.priority if item.priority in _PRIORITY_RANK else "normal",
            todo_id=item.todo_id,
        )
        db.add(row)
        created.append(row)
    await db.commit()
    for row in created:
        await db.refresh(row)
    logger.info("[DayPlan] agent=%s date=%s items=%d", agent_id, plan_date, len(created))
    return {"agent_id": agent_id, "plan_date": plan_date.isoformat(),
            "items": [_to_response(r) for r in created]}


@router.get("/agents/{agent_id}/day-plan")
async def get_day_plan(
    agent_id: str,
    date: date_cls | None = Query(None, description="Tag (YYYY-MM-DD), Standard: heute"),
    days: int = Query(1, ge=1, le=31, description="Anzahl Tage ab 'date'"),
    user=Depends(require_auth_or_agent),
    db: AsyncSession = Depends(get_db),
):
    """Den Plan eines Tages (oder einer Spanne) lesen — fuer Kalender und Sprachfront."""
    await _assert_access(agent_id, user, db)
    start = date or datetime.now(timezone.utc).date()
    end = start + timedelta(days=days - 1)
    rows = (await db.execute(
        select(AgentPlanItem)
        .where(AgentPlanItem.agent_id == agent_id,
               AgentPlanItem.plan_date >= start,
               AgentPlanItem.plan_date <= end)
        .order_by(AgentPlanItem.plan_date, AgentPlanItem.planned_start, AgentPlanItem.id)
    )).scalars().all()
    # Ohne feste Uhrzeit entscheidet die Prioritaet — sonst haengt die Reihenfolge am
    # Zufall der Eingabe, und 'hoch' landet hinter 'niedrig'.
    rows = sorted(
        rows,
        key=lambda r: (
            r.plan_date,
            0 if r.planned_start else 1,
            r.planned_start or datetime.min.replace(tzinfo=timezone.utc),
            _PRIORITY_RANK.get(r.priority, 1),
            r.id,
        ),
    )
    return {
        "agent_id": agent_id,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "items": [_to_response(r) for r in rows],
    }


@router.patch("/day-plan/{item_id}")
async def patch_plan_item(
    item_id: int,
    body: PlanItemPatch,
    user=Depends(require_auth_or_agent),
    db: AsyncSession = Depends(get_db),
):
    """Einen Block verschieben, umbenennen, abhaken oder streichen.

    Das ist die Haelfte, die den Plan erst nuetzlich macht: du kannst eingreifen, und
    der naechste Lauf des Agenten liest den korrigierten Plan.
    """
    row = await db.get(AgentPlanItem, item_id)
    if not row:
        raise HTTPException(status_code=404, detail="Plan item not found")
    await _assert_access(row.agent_id, user, db)

    if body.status is not None:
        if body.status not in VALID_STATUS:
            raise HTTPException(
                status_code=422,
                detail=f"Unbekannter Status — erlaubt: {', '.join(VALID_STATUS)}",
            )
        row.status = body.status
    if body.planned_start is not None:
        row.planned_start = body.planned_start
    if body.estimated_minutes is not None:
        row.estimated_minutes = body.estimated_minutes
    if body.title is not None:
        title = body.title.strip()
        if not title:
            raise HTTPException(status_code=422, detail="Titel darf nicht leer sein")
        row.title = title[:200]
    if body.notes is not None:
        row.notes = body.notes.strip()[:2000]
    await db.commit()
    await db.refresh(row)
    return _to_response(row)


@router.delete("/day-plan/{item_id}")
async def delete_plan_item(
    item_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Block ganz entfernen (Streichen mit Spur waere ``status='dropped'``)."""
    row = await db.get(AgentPlanItem, item_id)
    if not row:
        raise HTTPException(status_code=404, detail="Plan item not found")
    await _assert_access(row.agent_id, user, db)
    await db.delete(row)
    await db.commit()
    return {"ok": True}
