"""Nutzungsmessung (#1047): Aufrufe zählen, eigene Nutzung, Übersicht für die Verwaltung."""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import nutzung
from app.db.session import get_db
from app.dependencies import require_admin, require_auth

router = APIRouter(prefix="/nutzung", tags=["nutzung"])


class Aufruf(BaseModel):
    pfad: str = Field(..., max_length=300)


@router.post("/aufruf")
async def aufruf_zaehlen(body: Aufruf, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Zählt für den Angemeldeten selbst — eine fremde Person lässt sich nicht nennen.

    Antwort: der gezählte Bereich, ``null`` wenn der Pfad verworfen wurde.
    """
    return {"bereich": await nutzung.zaehlen(db, str(user.id), body.pfad)}


@router.get("/meine")
async def meine_nutzung(
    tage: int = Query(30, ge=1, le=nutzung.AUFBEWAHRUNG_TAGE),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Eigene Aufrufe je Bereich; das Menü holt oft Genutztes damit nach oben."""
    return {"tage": tage, "oft_ab": nutzung.OFT_GENUTZT,
            "bereiche": await nutzung.meine(db, str(user.id), tage)}


@router.get("/uebersicht")
async def uebersicht(
    tage: int = Query(30, ge=1, le=nutzung.AUFBEWAHRUNG_TAGE),
    _admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Nur für die Verwaltung, nur Summen je Bereich — keine Namen."""
    return {"tage": tage, "bereiche": await nutzung.uebersicht(db, tage)}
