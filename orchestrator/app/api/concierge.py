"""Admin-Concierge (#11) — eine Frage, eine Antwort, ohne durch fünf Seiten zu klicken.

Die Zahlen gab es alle schon: Systemzustand, Agenten, Aufgaben, Kosten, offene
Freigaben. Nur lagen sie auf fünf verschiedenen Seiten, und die häufigsten Fragen
eines Administrators („läuft alles?", „was kostet gerade was?", „wartet etwas auf
mich?") beantwortete keine davon direkt.

Bewusst KEIN eigener Agent und kein Sprachmodell dahinter. Ein Concierge, der eine
Zahl halluziniert, ist schlimmer als gar keiner — hier werden ausschließlich vorhandene
Abfragen zusammengesetzt und unverändert ausgeliefert.

Die Aktionen sind eine kurze, feste Liste. Alles, was Daten zerstören kann, ist NICHT
dabei: ein Widget in der Ecke ist der falsche Ort, um einen Agenten zu löschen.
"""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.log_redaction import scrub_log
from app.db.session import get_db
from app.dependencies import require_admin
from app.models.agent import Agent, AgentState
from app.models.task import Task, TaskStatus

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/concierge", tags=["concierge"])

# Nur das, was sich rückgängig machen lässt oder nichts zerstört. Löschen, Zurücksetzen
# und alles mit Datenverlust gehört auf die jeweilige Seite mit ihrer Rückfrage —
# nicht in ein Widget, das man im Vorbeigehen anklickt.
SAFE_ACTIONS = {
    "restart_agent": "Agenten neu starten",
    "stop_agent": "Agenten anhalten",
    "start_agent": "Agenten starten",
    "run_self_test": "Selbsttest starten",
}


class ActionRequest(BaseModel):
    action: str
    agent_id: str | None = None


@router.get("/overview")
async def concierge_overview(
    user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Der Zustand der Plattform in einer Antwort.

    Setzt vorhandene Abfragen zusammen — es entsteht keine zweite Wahrheit neben den
    Seiten, auf denen dieselben Zahlen stehen.
    """
    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(hours=24)

    agents = (await db.execute(select(Agent))).scalars().all()
    by_state: dict[str, int] = {}
    for agent in agents:
        key = str(getattr(agent.state, "value", agent.state))
        by_state[key] = by_state.get(key, 0) + 1

    unhealthy = [
        {"id": a.id, "name": a.name, "state": str(getattr(a.state, "value", a.state))}
        for a in agents
        if str(getattr(a.state, "value", a.state)) in ("error", "stopped")
    ]

    tasks_24h = (await db.execute(
        select(Task.status, func.count(Task.id)).where(Task.created_at >= day_ago)
        .group_by(Task.status)
    )).all()
    task_counts = {str(getattr(s, "value", s)): int(c) for s, c in tasks_24h}

    cost_24h = float((await db.execute(
        select(func.coalesce(func.sum(Task.cost_usd), 0)).where(Task.created_at >= day_ago)
    )).scalar() or 0)

    # Offene Freigaben: darauf wartet jemand — die gehören ganz nach oben.
    pending_approvals = 0
    try:
        from app.models.command_approval import ApprovalStatus, CommandApproval

        pending_approvals = int((await db.execute(
            select(func.count(CommandApproval.id))
            .where(CommandApproval.status == ApprovalStatus.PENDING)
        )).scalar() or 0)
    except Exception:  # noqa: BLE001
        logger.debug("Freigaben nicht zaehlbar", exc_info=True)

    # Hängende Aufgaben — dieselbe Schwelle wie der Watchdog, damit hier nicht eine
    # andere Zahl steht als in der Aufgabenliste.
    stale = 0
    try:
        from app.services.watchdog import _STALE_TASK_THRESHOLD

        stale = int((await db.execute(
            select(func.count(Task.id)).where(
                Task.status == TaskStatus.RUNNING,
                Task.started_at < now - _STALE_TASK_THRESHOLD,
            )
        )).scalar() or 0)
    except Exception:  # noqa: BLE001
        logger.debug("Haengende Aufgaben nicht zaehlbar", exc_info=True)

    # Eine Ampel statt einer Zahlenwueste: „laeuft alles?" ist die eigentliche Frage.
    if unhealthy or stale:
        verdict = "handlungsbedarf"
    elif pending_approvals:
        verdict = "wartet auf dich"
    else:
        verdict = "alles ruhig"

    return {
        "verdict": verdict,
        "agents": {"total": len(agents), "by_state": by_state, "unhealthy": unhealthy[:10]},
        "tasks_24h": {
            "total": sum(task_counts.values()),
            "failed": task_counts.get("failed", 0),
            "running": task_counts.get("running", 0),
            "stale": stale,
        },
        "cost_24h_usd": round(cost_24h, 4),
        "pending_approvals": pending_approvals,
        "actions": [{"id": k, "label": v} for k, v in SAFE_ACTIONS.items()],
    }


@router.post("/action")
async def concierge_action(
    body: ActionRequest,
    user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Eine der wenigen erlaubten Aktionen ausführen.

    Die Liste ist fest und wird hier geprüft, nicht in der Oberfläche: ein Widget, das
    nur die sicheren Knöpfe zeigt, ist keine Absicherung — jeder kann den Aufruf
    direkt schicken.
    """
    if body.action not in SAFE_ACTIONS:
        raise HTTPException(status_code=400, detail=f"Aktion nicht erlaubt: {body.action}")

    if body.action == "run_self_test":
        from app.services.self_test_service import SelfTestService

        result = await SelfTestService().execute_test_run()
        return {"action": body.action, "result": result}

    if not body.agent_id:
        raise HTTPException(status_code=400, detail="agent_id fehlt")

    agent = await db.get(Agent, body.agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent nicht gefunden")

    from app.core.agent_manager import AgentManager
    from app.dependencies import get_docker_service, get_redis_service  # noqa: F401

    # Der Manager braucht Docker und Redis; beide haengen am App-Zustand und werden
    # ueber die bestehenden Abhaengigkeiten geholt — kein zweiter Konstruktionsweg.
    from app.api import ws as ws_module

    docker = getattr(ws_module, "_docker", None)
    redis = getattr(ws_module, "_redis", None)
    if docker is None:
        raise HTTPException(status_code=503, detail="Docker nicht verfuegbar")
    manager = AgentManager(db, docker, redis)

    if body.action == "restart_agent":
        await manager.restart_agent(body.agent_id)
    elif body.action == "stop_agent":
        await manager.stop_agent(body.agent_id)
    elif body.action == "start_agent":
        await manager.start_agent(body.agent_id)

    logger.info("[Concierge] %s auf %s durch %s",
                scrub_log(body.action), scrub_log(body.agent_id), scrub_log(user.id))
    return {"action": body.action, "agent_id": body.agent_id, "status": "ok"}
