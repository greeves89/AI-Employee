"""Nutzungsmessung je Bereich (#1047): welche Teile der Oberfläche werden gebraucht?

Zwei Abnehmer, beide an derselben Zählung:

* das eigene Menü — was jemand in „Weitere Bereiche“ oft öffnet, steht bei ihm
  wieder oben (``meine``);
* die Verwaltung — welche Bereiche öffnet auf der Anlage niemand (``uebersicht``).
  Nur Summen je Bereich: Aufrufe und wie viele Personen, nie WER.

Gezählt wird der erste Pfadteil („/agents/abc“ → „/agents“), bei selbst
angelegten Seiten „/p/<kurzname>“. Was nicht wie ein Pfad der Oberfläche
aussieht, wird verworfen — die Tabelle ist kein Ablageplatz für beliebigen Text.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bereich_aufruf import BereichAufruf

logger = logging.getLogger(__name__)

#: So lange bleiben Zählungen liegen; ältere räumt der erste Aufruf des Tages weg.
AUFBEWAHRUNG_TAGE = 180
#: Ab so vielen Aufrufen im Zeitraum holt das Menü einen Bereich nach oben.
OFT_GENUTZT = 3
#: Mehr verschiedene Bereiche an einem Tag gibt die Oberfläche nicht her.
MAX_BEREICHE_JE_TAG = 60

_SEGMENT = r"[a-z0-9][a-z0-9-]{0,39}"
_PFAD = re.compile(rf"^/({_SEGMENT})(?:/({_SEGMENT}))?")

_letzte_bereinigung: date | None = None


def bereich_aus_pfad(pfad: str | None) -> str | None:
    """„/agents/abc?x=1“ → „/agents“, „/p/kunde/x“ → „/p/kunde“, Unsinn → None."""
    if not pfad or len(pfad) > 300:
        return None
    treffer = _PFAD.match(pfad.strip().lower())
    if not treffer:
        return None
    erster, zweiter = treffer.group(1), treffer.group(2)
    if erster == "p":
        return f"/p/{zweiter}" if zweiter else None
    return f"/{erster}"


def _heute() -> date:
    return datetime.now(timezone.utc).date()


async def _alte_loeschen(db: AsyncSession, heute: date) -> None:
    """Einmal je Tag und Prozess: Zählungen jenseits der Frist entfernen."""
    global _letzte_bereinigung
    if _letzte_bereinigung == heute:
        return
    _letzte_bereinigung = heute
    await db.execute(delete(BereichAufruf).where(
        BereichAufruf.tag < heute - timedelta(days=AUFBEWAHRUNG_TAGE)))


def _einfuegen(db: AsyncSession):
    if db.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert


async def zaehlen(db: AsyncSession, user_id: str, pfad: str, heute: date | None = None) -> str | None:
    """Einen Aufruf zählen. Gibt den gezählten Bereich zurück (None = verworfen)."""
    bereich = bereich_aus_pfad(pfad)
    if bereich is None:
        return None
    heute = heute or _heute()
    await _alte_loeschen(db, heute)
    vorhanden = await db.scalar(select(BereichAufruf.id).where(
        BereichAufruf.user_id == user_id, BereichAufruf.bereich == bereich, BereichAufruf.tag == heute))
    if vorhanden is None:
        heute_schon = await db.scalar(select(func.count()).select_from(BereichAufruf).where(
            BereichAufruf.user_id == user_id, BereichAufruf.tag == heute))
        if (heute_schon or 0) >= MAX_BEREICHE_JE_TAG:
            return None
    insert = _einfuegen(db)
    anweisung = insert(BereichAufruf).values(user_id=user_id, bereich=bereich, tag=heute, anzahl=1)
    anweisung = anweisung.on_conflict_do_update(
        index_elements=["user_id", "bereich", "tag"],
        set_={"anzahl": BereichAufruf.anzahl + 1},
    )
    await db.execute(anweisung)
    await db.commit()
    return bereich


async def meine(db: AsyncSession, user_id: str, tage: int = 30, heute: date | None = None) -> dict[str, int]:
    """Eigene Aufrufe je Bereich im Zeitraum."""
    heute = heute or _heute()
    zeilen = await db.execute(
        select(BereichAufruf.bereich, func.sum(BereichAufruf.anzahl))
        .where(BereichAufruf.user_id == user_id, BereichAufruf.tag > heute - timedelta(days=tage))
        .group_by(BereichAufruf.bereich))
    return {b: int(n or 0) for b, n in zeilen.all()}


async def uebersicht(db: AsyncSession, tage: int = 30, heute: date | None = None) -> list[dict]:
    """Anlagenweit je Bereich: Aufrufe, Personen, zuletzt — ohne Namen."""
    heute = heute or _heute()
    zeilen = await db.execute(
        select(BereichAufruf.bereich,
               func.sum(BereichAufruf.anzahl),
               func.count(func.distinct(BereichAufruf.user_id)),
               func.max(BereichAufruf.tag))
        .where(BereichAufruf.tag > heute - timedelta(days=tage))
        .group_by(BereichAufruf.bereich))
    ergebnis = [
        {"bereich": b, "aufrufe": int(n or 0), "personen": int(p or 0),
         "zuletzt": z.isoformat() if z else None}
        for b, n, p, z in zeilen.all()
    ]
    ergebnis.sort(key=lambda e: (-e["aufrufe"], e["bereich"]))
    return ergebnis
