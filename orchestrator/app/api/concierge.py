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

from app.core import attention
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


async def _collect_attention(
    db: AsyncSession,
    agents: list,
    now: datetime,
    stale: int,
    resting: list[dict],
    broken_agents: list[dict],
) -> list[dict]:
    """Alles einsammeln, was jemanden braucht — jeder Erkenner für sich gekapselt.

    Jeder Block fängt seine eigenen Fehler. Ein Concierge, der wegen einer fehlenden
    Tabelle gar nichts mehr zeigt, ist schlechter als einer, der einen Punkt
    weglässt — und die Punkte sind bewusst unabhängig voneinander.
    """
    items: list[dict] = []

    # 1 · Eskalationen. Der einzige Fall, in dem ein Agent buchstäblich stillsteht
    #     und auf einen Menschen wartet — der gehört ganz nach oben.
    try:
        from app.models.command_approval import ApprovalStatus, CommandApproval

        pending = (await db.execute(
            select(CommandApproval).where(CommandApproval.status == ApprovalStatus.PENDING)
        )).scalars().all()
        escalations = [
            a for a in pending
            if str((a.meta or {}).get("kind") or "") in ("escalation", "low_confidence")
        ]
        if escalations:
            unsure = sum(1 for a in escalations if (a.meta or {}).get("kind") == "low_confidence")
            items.append(attention.item(
                "escalation", attention.WAITING,
                f"{len(escalations)} Eskalation(en)",
                (f"{unsure} × unsicher, {len(escalations) - unsure} × gescheitert — "
                 "ein Agent wartet auf deine Entscheidung."),
                link="/approvals", count=len(escalations),
            ))
        rest = len(pending) - len(escalations)
        if rest:
            items.append(attention.item(
                "approval", attention.WAITING,
                f"{rest} offene Freigabe(n)",
                "Ein Agent hat gefragt und wartet.",
                link="/approvals", count=rest,
            ))
    except Exception:  # noqa: BLE001
        logger.debug("Freigaben nicht auswertbar", exc_info=True)

    # 2 · Agenten im Fehlerzustand.
    for entry in broken_agents:
        items.append(attention.item(
            "agent_error", attention.BROKEN,
            entry["name"], "Fehlerzustand — der Agent arbeitet nicht.",
            agent_id=entry["id"], action="restart_agent", action_label="Neu starten",
            link=f"/agents/{entry['id']}",
        ))

    # 3 · Hängende Aufgaben.
    if stale:
        items.append(attention.item(
            "stale_task", attention.BROKEN,
            f"{stale} hängende Aufgabe(n)",
            "Seit über 30 Minuten ohne Lebenszeichen.",
            link="/tasks", count=stale,
        ))

    # 4 · Abgelaufene Zugänge. Der wertvollste Punkt, weil er STILL scheitert:
    #     nichts wird rot, es hört einfach auf zu funktionieren.
    try:
        from app.models.oauth_integration import OAuthIntegration

        for row in (await db.execute(select(OAuthIntegration))).scalars().all():
            state = attention.token_state(row.expires_at, now)
            if state is None:
                continue
            provider = str(getattr(row.provider, "value", row.provider))
            items.append(attention.item(
                "expired_access", state,
                f"Zugang {provider}"
                + (f" ({row.account_label})" if row.account_label else ""),
                ("abgelaufen — Aufrufe darüber scheitern still"
                 if state == attention.BROKEN
                 else "läuft in den nächsten Tagen ab"),
                link="/integrations",
            ))
    except Exception:  # noqa: BLE001
        logger.debug("Zugaenge nicht pruefbar", exc_info=True)

    # 5 · KI-Konten, deren letzte Pruefung fehlschlug.
    try:
        from app.models.ai_account import AIAccount

        for row in (await db.execute(
            select(AIAccount).where(AIAccount.is_active.is_(True))
        )).scalars().all():
            if not row.last_status or row.last_status == "ok":
                continue
            items.append(attention.item(
                "account_unhealthy", attention.BROKEN,
                f"KI-Konto „{row.name}“",
                f"Letzte Prüfung: {row.last_status}"
                + (f" — {row.last_error}" if row.last_error else ""),
                link="/ai-accounts",
            ))
    except Exception:  # noqa: BLE001
        logger.debug("KI-Konten nicht pruefbar", exc_info=True)

    # 6 · Budgets. Aufgebraucht heisst je nach Einstellung: heruntergestuft oder
    #     gestoppt — beides sollte man wissen, bevor jemand fragt.
    try:
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        spent_rows = (await db.execute(
            select(Task.agent_id, func.coalesce(func.sum(Task.cost_usd), 0))
            .where(Task.created_at >= month_start, Task.agent_id.isnot(None))
            .group_by(Task.agent_id)
        )).all()
        spent = {aid: float(total or 0) for aid, total in spent_rows}
        for agent in agents:
            state = attention.budget_state(spent.get(agent.id), agent.budget_usd)
            if state is None:
                continue
            items.append(attention.item(
                "budget", state, agent.name,
                (f"Monatsbudget aufgebraucht ({spent.get(agent.id, 0):.2f} von "
                 f"{agent.budget_usd:.2f} $) — Folge: {agent.budget_exceeded_action}"
                 if state == attention.BROKEN
                 else f"Monatsbudget zu über 90 % verbraucht "
                      f"({spent.get(agent.id, 0):.2f} von {agent.budget_usd:.2f} $)"),
                agent_id=agent.id, link=f"/agents/{agent.id}",
            ))
    except Exception:  # noqa: BLE001
        logger.debug("Budgets nicht auswertbar", exc_info=True)

    # 7 · Angehalten, aber mit Auftrag: tut still nichts.
    for entry in resting:
        if not entry["skips_proactive"]:
            continue
        items.append(attention.item(
            "idle_with_duties", attention.WAITING,
            entry["name"],
            "angehalten, hat aber Verantwortungsbereiche — proaktive Läufe fallen aus.",
            agent_id=entry["id"], action="start_agent", action_label="Starten",
            link=f"/agents/{entry['id']}",
        ))

    # Kaputtes zuerst, danach in der Reihenfolge des Einsammelns — Eskalationen
    # stehen dadurch vor den restlichen Wartepunkten.
    items.sort(key=lambda i: 0 if i["severity"] == attention.BROKEN else 1)
    return items[:20]


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

    def _state(a) -> str:  # noqa: ANN001
        return str(getattr(a.state, "value", a.state))

    # „Kaputt" und „aus" sind NICHT dasselbe, und sie in einen Topf zu werfen war
    # der Grund, weshalb hier dauerhaft Handlungsbedarf stand.
    #
    # Angehalten ist ein **normaler** Zustand: der Nutzer hält einen Agenten an, der
    # Idle-Stopp hält ihn an, und beim nächsten Auftrag weckt ``wake_agent`` ihn
    # wieder. Eine Ampel, die bei jedem ruhenden Agenten rot zeigt, ist nach einer
    # Woche eine Ampel, die niemand mehr ansieht.
    broken = [
        {"id": a.id, "name": a.name, "state": _state(a)}
        for a in agents
        if _state(a) == "error"
    ]

    # Ruhende Agenten sind eine Auskunft, kein Alarm. EINE Ausnahme: ein angehaltener
    # Agent mit Verantwortungsbereichen bekommt keine proaktiven Läufe mehr (seit
    # v1.154.1 werden gestoppte Agenten nicht mehr angesteuert). Der tut dann still
    # nichts — das gehört gesagt, aber als Hinweis, nicht als Notfall.
    resting = []
    for a in agents:
        if _state(a) != "stopped":
            continue
        proactive = (a.config or {}).get("proactive") or {}
        skips_work = bool(proactive.get("enabled") and proactive.get("responsibilities"))
        resting.append({
            "id": a.id,
            "name": a.name,
            "state": "stopped",
            "skips_proactive": skips_work,
        })

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

    # ── Die Liste: nur, was eine Entscheidung oder einen Handgriff braucht ────
    items = await _collect_attention(db, agents, now, stale, resting, broken)
    verdict = attention.verdict_for(items)

    return {
        "verdict": verdict,
        "items": items,
        # Die Zahlen bleiben — aber als Fussnote, nicht als Hauptinhalt. Sie
        # verlangen keine Handlung; dafuer gibt es das Dashboard.
        "stats": {
            "agents": len(agents),
            "resting": len(resting),
            "tasks_24h": sum(task_counts.values()),
            "failed_24h": task_counts.get("failed", 0),
            "cost_24h_usd": round(cost_24h, 2),
        },
        "agents": {
            "total": len(agents),
            "by_state": by_state,
            "broken": broken[:10],
            "resting": resting[:10],
            # Alte Bezeichnung, damit eine Oberflaeche aus der Zeit davor nicht
            # ploetzlich eine leere Liste sieht. Enthaelt jetzt NUR noch echte
            # Fehler — das war ja der Punkt.
            "unhealthy": broken[:10],
        },
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
