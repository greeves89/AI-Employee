"""Audit log API - query and record privileged command executions."""

import csv
import io
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AKTEURE_OHNE_AGENT, NUR_SERVERSEITIG, protokolliere
from app.core.log_redaction import scrub_log
from app.db.session import get_db
from app.core.ownership import is_admin, visible_agent_ids
from app.dependencies import require_auth, verify_agent_token
from app.models.audit_log import AuditLog, AuditEventType

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/audit", tags=["audit"])


# ══════════════════════════════════════════════════════════════════════════════
# SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════

class AuditLogCreate(BaseModel):
    """Agent reports a command execution for audit."""
    event_type: str  # AuditEventType value
    command: Optional[str] = None
    outcome: str = "success"   # success, failure, blocked
    exit_code: Optional[int] = None
    task_id: Optional[str] = None
    approval_id: Optional[str] = None
    meta: Optional[dict] = None


class AuditLogResponse(BaseModel):
    id: int
    agent_id: str
    task_id: Optional[str]
    approval_id: Optional[str]
    event_type: str
    command: Optional[str]
    outcome: str
    exit_code: Optional[int]
    user_id: Optional[str]
    meta: Optional[dict]
    created_at: datetime

    class Config:
        from_attributes = True


# ══════════════════════════════════════════════════════════════════════════════
# AGENT ENDPOINTS (Write)
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/log", status_code=201)
async def create_audit_log(
    body: AuditLogCreate,
    agent_auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    """
    Agent reports a privileged command execution.

    Called automatically after executing any sudo/privileged command,
    approved tool calls, or blocked attempts.
    """
    agent_id = agent_auth["agent_id"]

    # Validate event_type. Was ein Mensch oder die Plattform entscheidet (Antworten,
    # Freigaben, Anmeldungen, Verwaltung), traegt nur der Server ein — sonst koennte
    # ein Agent sich eine Freigabe ins Protokoll schreiben, die es nie gab (#908).
    valid_types = {e.value for e in AuditEventType} - NUR_SERVERSEITIG
    if body.event_type not in valid_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid event_type. Must be one of: {sorted(valid_types)}",
        )

    entry = AuditLog(
        agent_id=agent_id,
        task_id=body.task_id,
        approval_id=body.approval_id,
        event_type=body.event_type,
        command=body.command,
        outcome=body.outcome,
        exit_code=body.exit_code,
        meta=body.meta,
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)

    logger.info(
        f"Audit: agent={scrub_log(agent_id)} event={scrub_log(body.event_type)} outcome={scrub_log(body.outcome)} "
        f"cmd={scrub_log(body.command)!r:.80}"
    )

    return {"id": entry.id, "status": "logged"}


# ══════════════════════════════════════════════════════════════════════════════
# USER ENDPOINTS (Read)
# ══════════════════════════════════════════════════════════════════════════════

#: Lesbare Namen fuer Eintraege ohne Agentenbezug.
_AKTEUR_LABEL = {
    "auth": "Anmeldung",
    "admin": "Verwaltung",
    "system": "System",
    "global": "Alle Agenten",
    "user": "Nutzer",
}


async def _gefiltert(
    user, db: AsyncSession, *, agent_id, task_id, event_type, outcome, since, until,
):
    """Eine Abfrage fuer Liste UND Export — dieselben Filter, dieselbe Sichtbarkeit."""
    base = select(AuditLog)
    # Nur Eintraege der Agenten, die der Aufrufer sehen darf. Eintraege ohne
    # Agentenbezug ("auth", "admin", "global" …) gehoeren damit allein den
    # Administratoren — auch Anmeldeereignisse.
    sichtbar = await visible_agent_ids(user, db)
    if sichtbar is not None:
        base = base.where(AuditLog.agent_id.in_(sichtbar))
    if agent_id:
        base = base.where(AuditLog.agent_id == agent_id)
    if task_id:
        base = base.where(AuditLog.task_id == task_id)
    if event_type:
        base = base.where(AuditLog.event_type == event_type)
    if outcome:
        base = base.where(AuditLog.outcome == outcome)
    if since:
        base = base.where(AuditLog.created_at >= since)
    if until:
        base = base.where(AuditLog.created_at <= until)
    return base


async def _anreichern(db: AsyncSession, logs) -> list[dict]:
    """Agentenname und Person serverseitig aufloesen.

    Im Browser liessen sich nur die EIGENEN Agenten aufloesen — ein Administrator
    sah fuer fremde Agenten nur Kennungen, fuer geloeschte sowieso. Reihenfolge:
    lebender Agent → beim Schreiben gemerkter Name → Kennung.
    """
    from app.models.agent import Agent
    from app.models.user import User

    agent_ids = {e.agent_id for e in logs if e.agent_id and e.agent_id not in AKTEURE_OHNE_AGENT}
    agenten = dict((await db.execute(
        select(Agent.id, Agent.name).where(Agent.id.in_(agent_ids))
    )).all()) if agent_ids else {}
    user_ids = {e.user_id for e in logs if e.user_id}
    personen = {uid: (name or mail) for uid, name, mail in (await db.execute(
        select(User.id, User.name, User.email).where(User.id.in_(user_ids))
    )).all()} if user_ids else {}

    out = []
    for e in logs:
        meta = e.meta or {}
        if e.agent_id in _AKTEUR_LABEL:
            agent_name = _AKTEUR_LABEL[e.agent_id]
        else:
            agent_name = agenten.get(e.agent_id) or meta.get("agent_name") or e.agent_id
        person = personen.get(e.user_id) if e.user_id else None
        if e.user_id and not person:
            person = meta.get("user_email") or meta.get("email") or e.user_id   # geloeschter Nutzer
        out.append({
            "id": e.id,
            "agent_id": e.agent_id,
            "agent_name": agent_name,
            "task_id": e.task_id,
            "approval_id": e.approval_id,
            "event_type": e.event_type,
            "outcome": e.outcome,
            "command": e.command,
            "exit_code": e.exit_code,
            "user_id": e.user_id,
            "person": person,
            "details": e.meta,   # legacy key
            "meta": e.meta,      # canonical key the frontend reads
            "created_at": e.created_at.isoformat() if e.created_at else None,
        })
    return out


@router.get("/logs")
async def list_audit_logs(
    agent_id: Optional[str] = Query(None, description="Filter by agent ID"),
    task_id: Optional[str] = Query(None, description="Filter by task ID"),
    event_type: Optional[str] = Query(None, description="Filter by event type"),
    outcome: Optional[str] = Query(None, description="Filter by outcome (success/failure/blocked)"),
    since: Optional[datetime] = Query(None, description="Return entries after this timestamp"),
    until: Optional[datetime] = Query(None, description="Return entries up to this timestamp"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Query audit log entries. Returns {logs: [...], total: N} for pagination.
    """
    base = await _gefiltert(user, db, agent_id=agent_id, task_id=task_id, event_type=event_type,
                            outcome=outcome, since=since, until=until)
    total = await db.scalar(select(func.count()).select_from(base.subquery()))
    result = await db.execute(base.order_by(desc(AuditLog.created_at), desc(AuditLog.id))
                              .offset(offset).limit(limit))
    logs = result.scalars().all()
    return {"logs": await _anreichern(db, logs), "total": total or 0}


#: Obergrenze fuer einen Export. Darueber filtern — eine Datei mit Millionen
#: Zeilen oeffnet ohnehin niemand.
EXPORT_MAX_ZEILEN = 50_000

_EXPORT_SPALTEN = [
    ("created_at", "Zeitpunkt (UTC)"),
    ("event_type", "Ereignis"),
    ("agent_name", "Agent"),
    ("agent_id", "Agent-ID"),
    ("person", "Person"),
    ("user_id", "Personen-ID"),
    ("command", "Befehl / Vorgang"),
    ("outcome", "Ergebnis"),
    ("approval_id", "Freigabe-ID"),
    ("task_id", "Aufgaben-ID"),
    ("meta", "Details"),
]


def _csv_zelle(wert) -> str:
    """Zellinhalt so entschärfen, dass Excel/LibreOffice ihn nie als Formel ausführt.

    Befehle und Antworten im Protokoll stammen von Agenten und Nutzern — ein Eintrag
    wie ``=HYPERLINK(...)`` würde beim Öffnen sonst ausgewertet (CSV-Formel-Einschleusung).
    """
    text = "" if wert is None else str(wert)
    if text and text[0] in "=+-@\t\r":
        return "'" + text
    return text


@router.get("/logs/export")
async def export_audit_logs(
    agent_id: Optional[str] = Query(None),
    task_id: Optional[str] = Query(None),
    event_type: Optional[str] = Query(None),
    outcome: Optional[str] = Query(None),
    since: Optional[datetime] = Query(None),
    until: Optional[datetime] = Query(None),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Protokoll als CSV — nur fuer Administratoren, mit denselben Filtern wie die Liste.

    UTF-8 mit BOM und Semikolon: so oeffnet Excel die Datei mit deutschen
    Einstellungen direkt richtig, Umlaute inklusive. Der Export selbst wird
    protokolliert (wer hat wann was mitgenommen).
    """
    if not is_admin(user):
        raise HTTPException(status_code=403, detail="Nur Administratoren dürfen das Protokoll exportieren.")
    import json as _json

    base = await _gefiltert(user, db, agent_id=agent_id, task_id=task_id, event_type=event_type,
                            outcome=outcome, since=since, until=until)
    logs = (await db.execute(
        base.order_by(desc(AuditLog.created_at), desc(AuditLog.id)).limit(EXPORT_MAX_ZEILEN)
    )).scalars().all()
    zeilen = await _anreichern(db, logs)

    puffer = io.StringIO()
    writer = csv.writer(puffer, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    writer.writerow([titel for _, titel in _EXPORT_SPALTEN])
    for z in zeilen:
        writer.writerow([
            _csv_zelle(_json.dumps(z[k], ensure_ascii=False, default=str) if k == "meta" and z[k] else z[k])
            for k, _ in _EXPORT_SPALTEN
        ])

    filter_ = {k: v for k, v in {
        "agent_id": agent_id, "task_id": task_id, "event_type": event_type,
        "outcome": outcome, "since": since.isoformat() if since else None,
        "until": until.isoformat() if until else None,
    }.items() if v}
    await protokolliere(db, AuditEventType.AUDIT_EXPORTED, user_id=user.id,
                        command="Prüfprotokoll exportiert (CSV)",
                        meta={"filter": filter_, "zeilen": len(zeilen)})
    await db.commit()

    stempel = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    return Response(
        content=("\ufeff" + puffer.getvalue()).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="pruefprotokoll-{stempel}.csv"'},
    )


@router.get("/logs/summary")
async def audit_summary(
    agent_id: Optional[str] = Query(None),
    since: Optional[datetime] = Query(None),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Return aggregate counts grouped by event_type and outcome.
    Useful for dashboards and compliance reporting.
    """
    stmt = (
        select(
            AuditLog.event_type,
            AuditLog.outcome,
            func.count(AuditLog.id).label("count"),
        )
        .group_by(AuditLog.event_type, AuditLog.outcome)
        .order_by(AuditLog.event_type, AuditLog.outcome)
    )

    sichtbar = await visible_agent_ids(user, db)
    if sichtbar is not None:
        stmt = stmt.where(AuditLog.agent_id.in_(sichtbar))
    if agent_id:
        stmt = stmt.where(AuditLog.agent_id == agent_id)
    if since:
        stmt = stmt.where(AuditLog.created_at >= since)

    result = await db.execute(stmt)
    rows = result.all()

    # Aggregate into frontend-friendly shape
    total = sum(r.count for r in rows)
    by_outcome: dict[str, int] = {"success": 0, "failure": 0, "blocked": 0}
    by_event_type: dict[str, int] = {}
    for r in rows:
        if r.outcome and r.outcome in by_outcome:
            by_outcome[r.outcome] += r.count
        et = r.event_type or "UNKNOWN"
        by_event_type[et] = by_event_type.get(et, 0) + r.count

    return {
        "total": total,
        "by_outcome": by_outcome,
        "by_event_type": by_event_type,
        "detail": [
            {"event_type": r.event_type, "outcome": r.outcome, "count": r.count}
            for r in rows
        ],
    }


@router.get("/logs/{log_id}", response_model=AuditLogResponse)
async def get_audit_log(
    log_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve a single audit log entry by ID."""
    entry = await db.get(AuditLog, log_id)
    sichtbar = await visible_agent_ids(user, db)
    # Dieselbe Antwort fuer „gibt es nicht" und „gehoert dir nicht": sonst liesse
    # sich ueber die fortlaufende Nummer abzaehlen, was andere Nutzer tun.
    if not entry or (sichtbar is not None and entry.agent_id not in sichtbar):
        raise HTTPException(status_code=404, detail="Audit log entry not found")
    return entry
