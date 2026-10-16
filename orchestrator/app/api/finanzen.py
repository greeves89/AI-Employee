"""Finanzen: Kosten je Monat, Projekt und Agent, Projektbudgets, CSV (core/finanzen)."""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import require_auth

router = APIRouter(prefix="/finanzen", tags=["finanzen"])


@router.get("")
async def finanzen_uebersicht(monate: int = Query(6, ge=1, le=24), user=Depends(require_auth),
                              db: AsyncSession = Depends(get_db)):
    from app.core import finanzen

    return await finanzen.uebersicht(db, user, monate)


@router.get("/export.csv")
async def finanzen_export(monate: int = Query(12, ge=1, le=24), user=Depends(require_auth),
                          db: AsyncSession = Depends(get_db)):
    from app.core import finanzen

    daten = await finanzen.uebersicht(db, user, monate)
    return Response(
        content="﻿" + finanzen.als_csv(daten), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="finanzen-{daten["monat"]}.csv"'},
    )


class ProjektBudget(BaseModel):
    # USD je Kalendermonat; leer oder 0 = kein Budget.
    budget_usd: float | None = Field(default=None, ge=0, le=1_000_000)


@router.put("/projekte/{team_id}/budget")
async def projekt_budget_setzen(team_id: str, body: ProjektBudget, user=Depends(require_auth),
                                db: AsyncSession = Depends(get_db)):
    """Monatsbudget eines Projekts — nur der Ersteller (auch kein Administrator)."""
    from app.api.teams import _ist_ersteller, _is_team_member, _kein_betrachter
    from app.models.team import Team

    _kein_betrachter(user)
    t = await db.get(Team, team_id)
    if t is None or not t.is_active or not await _is_team_member(t, user, db):
        raise HTTPException(status_code=404, detail="Projekt nicht gefunden.")
    if not _ist_ersteller(t, user):
        raise HTTPException(status_code=403, detail="Das Budget setzt, wer das Projekt angelegt hat.")
    t.budget_usd = round(body.budget_usd, 2) if body.budget_usd else None
    await db.commit()
    return {"id": t.id, "budget_usd": t.budget_usd}
