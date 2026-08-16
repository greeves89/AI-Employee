"""Was passiert, wenn ein Agent ausfaellt oder ein Mensch nicht antwortet.

Eine Kette fuer beide Faelle (siehe ``core/agent_duty``):
  * Agent faellt aus  → Vertreter uebernimmt seine offenen Todos, Besitzer wird informiert.
  * Mensch schweigt   → nach zwei unbeantworteten Rueckfragen geht es eine Stufe hoeher.

Beides nutzt, was schon da ist: den Watchdog fuer haengende Aufgaben, die bestehende
Todo-Tabelle fuer die Uebergabe, ``Notification`` fuer die Meldung und ``teams`` fuer den
Lead. Kein zweites Aufgabensystem, keine zweite Meldeschiene.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import agent_duty as duty_core
from app.core.log_redaction import scrub_log
from app.models.agent import Agent
from app.models.agent_todo import AgentTodo, TodoStatus
from app.models.notification import Notification

logger = logging.getLogger(__name__)

# Eine Uebergabe pro Agent und Tag reicht — sonst schiebt jeder Scheduler-Tick die
# Todos erneut hin und her und der Besitzer bekommt im Minutentakt Post.
HANDOVER_COOLDOWN_SECONDS = 12 * 3600
# So lange gilt eine Rueckfrage als unbeantwortet, bevor sie eskaliert.
UNANSWERED_AFTER = timedelta(hours=12)
# Eine Ueberlast-Meldung pro Agent und Stunde reicht — ein taeglicher Cron-Zeitplan
# ruehrt sich ohnehin erst am naechsten Tag wieder, aber ein kurzgetakteter (alle paar
# Minuten) wuerde ohne Drossel bei anhaltender Ueberlast jedes Mal erneut melden.
OVERLOAD_ALERT_COOLDOWN_SECONDS = 3600


async def team_lead_for(db: AsyncSession, agent_id: str) -> str:
    """Der Team-Lead dieses Agenten, oder "" — letzte Stufe vor der Administration.

    Die Mitgliedschaft steht als Liste in ``Team.member_agent_ids``; eine
    ``team_members``-Tabelle gibt es nicht. Der frueher hier stehende JOIN darauf
    lief ausnahmslos in den ImportError und wurde vom ``except`` verschluckt —
    die Team-Lead-Stufe der Vertretungskette hat damit nie ausgeloest. Gefiltert
    wird wie ueberall sonst mit ``_teams_for_agent`` in Python, weil sich
    JSONB-Containment je nach Treiber unterschiedlich verhaelt.
    """
    try:
        from app.api.mcp_agent import _teams_for_agent
        from app.models.team import Team
        teams = (await db.execute(
            select(Team).where(Team.is_active.is_(True))
        )).scalars().all()
        for team in _teams_for_agent(list(teams), agent_id):
            lead = team.lead_agent_id
            if lead and lead != agent_id:
                return lead
    except Exception:  # noqa: BLE001 — ohne Team gibt es eben keinen Lead
        logger.debug("Team-Lead-Suche fehlgeschlagen fuer %s", scrub_log(agent_id), exc_info=True)
    return ""


async def resolve_deputy(db: AsyncSession, agent: Agent) -> Agent | None:
    """Wer uebernimmt: der eingetragene Vertreter, sonst der Team-Lead.

    Der Vertreter muss selbst arbeitsfaehig sein — einen zweiten toten Agenten zu
    beauftragen waere schlimmer als gar keine Vertretung, weil es wie Erledigung aussieht.
    """
    for candidate_id in (duty_core.deputy_agent_id(agent), await team_lead_for(db, agent.id)):
        if not candidate_id or candidate_id == agent.id:
            continue
        candidate = (await db.execute(
            select(Agent).where(Agent.id == candidate_id)
        )).scalar_one_or_none()
        if candidate is None:
            continue
        state = str(getattr(candidate.state, "value", candidate.state)).lower()
        if state in ("running", "idle", "working"):
            return candidate
        logger.info("Vertreter %s faellt aus (%s) — naechste Stufe", candidate_id, state)
    return None


async def handover_open_work(db: AsyncSession, agent: Agent, deputy: Agent, reason: str) -> int:
    """Offene Todos des ausgefallenen Agenten dem Vertreter geben. Gibt die Anzahl zurueck.

    Uebertragen wird die BESTEHENDE Todo-Zeile (Titel, Beschreibung, Projekt), ergaenzt um
    eine Herkunftszeile — so bleibt nachvollziehbar, wessen Arbeit das war und warum sie
    gewandert ist.
    """
    open_todos = (await db.execute(
        select(AgentTodo).where(
            AgentTodo.agent_id == agent.id,
            AgentTodo.status.in_((TodoStatus.PENDING, TodoStatus.IN_PROGRESS)),
        )
    )).scalars().all()
    if not open_todos:
        return 0

    marker = f"[Übernommen von {agent.name} — {reason}]"
    for todo in open_todos:
        todo.agent_id = deputy.id
        todo.status = TodoStatus.PENDING          # laufende Arbeit beginnt beim Vertreter neu
        existing = (todo.description or "").strip()
        if marker not in existing:
            todo.description = f"{marker}\n{existing}".strip()
    await db.flush()
    logger.info(
        "[Duty] %d Todo(s) von %s an %s uebergeben (%s)",
        len(open_todos), agent.id, deputy.id, reason,
    )
    return len(open_todos)


async def escalate_failure(db: AsyncSession, redis, agent: Agent, duty: dict) -> dict:
    """Ausfall behandeln: Vertreter suchen, Arbeit uebergeben, EINE Meldung absetzen.

    Gibt ``{"handled": bool, "deputy": id|"", "todos": int}`` zurueck. Gedrosselt, damit
    ein tagelang toter Agent nicht jeden Tick erneut uebergibt.
    """
    key = f"duty:handover:{agent.id}"
    try:
        if not await redis.client.set(key, "1", nx=True, ex=HANDOVER_COOLDOWN_SECONDS):
            return {"handled": False, "deputy": "", "todos": 0, "throttled": True}
    except Exception:  # noqa: BLE001
        logger.debug("[Duty] Drossel nicht verfuegbar", exc_info=True)

    reason = duty.get("reason") or "ausgefallen"
    deputy = await resolve_deputy(db, agent)
    moved = 0
    if deputy is not None:
        moved = await handover_open_work(db, agent, deputy, reason)

    if deputy is None:
        title = f"{agent.name} ist ausgefallen — niemand übernimmt"
        message = (
            f"{reason}. Es ist kein Vertreter hinterlegt und kein Team-Lead erreichbar, "
            f"die offene Arbeit bleibt liegen. Trage in den Einstellungen des Agenten "
            f"einen Vertreter ein oder kümmere dich selbst darum."
        )
        priority = "high"
    elif moved:
        title = f"{agent.name} ist ausgefallen — {deputy.name} übernimmt"
        message = (
            f"{reason}. {moved} offene Aufgabe(n) sind an {deputy.name} übergegangen und "
            f"stehen dort wieder auf offen."
        )
        priority = "high"
    else:
        title = f"{agent.name} ist ausgefallen"
        message = f"{reason}. Es lag keine offene Aufgabe vor, es geht also nichts verloren."
        priority = "normal"

    db.add(Notification(
        agent_id=agent.id,
        type="error" if deputy is None else "warning",
        title=title[:200],
        message=message[:240],
        priority=priority,
        action_url=f"/agents/{agent.id}?tab=settings",
        meta={"reason": "duty_failure", "deputy": deputy.id if deputy else "",
              "todos_moved": moved},
    ))
    return {"handled": True, "deputy": deputy.id if deputy else "", "todos": moved}


async def escalate_overload(db: AsyncSession, redis, agent: Agent, duty: dict,
                             schedule_name: str) -> bool:
    """Ueberlast-Skip melden: EINE Meldung pro Agent und Stunde, sonst still.

    Der Agent lebt, er ist nur beschaeftigt — anders als bei ``escalate_failure`` gibt es
    hier keinen Vertreter, der uebernehmen muesste. Ohne diese Meldung verschwindet ein
    uebersprungener Zeitplan aber spurlos: ``next_run_at`` wandert im Scheduler still
    weiter, es steht nur eine ``logger.info``-Zeile da, die im (nur WARNING/ERROR)
    Fehler-Log gar nicht auftaucht. Ein einzelner ueberlasteter Tick reicht so, um einen
    taeglichen Job (z.B. den 06:00-Podcast) komplett ausfallen zu lassen, ohne dass Nutzer
    oder Agent je davon erfahren. Root-caused via #605.
    """
    key = f"duty:overload:{agent.id}"
    try:
        if not await redis.client.set(key, "1", nx=True, ex=OVERLOAD_ALERT_COOLDOWN_SECONDS):
            return False
    except Exception:  # noqa: BLE001
        logger.debug("[Duty] Ueberlast-Drossel nicht verfuegbar", exc_info=True)

    db.add(Notification(
        agent_id=agent.id,
        type="warning",
        title=f"{agent.name} ist ueberlastet — Zeitplan uebersprungen"[:200],
        message=(
            f"'{schedule_name}' wurde nicht ausgefuehrt: {duty.get('reason')}. Der Zeitplan "
            f"laeuft normal weiter, aber dieser Durchgang faellt aus."
        )[:240],
        priority="normal",
        action_url=f"/agents/{agent.id}",
        meta={"reason": "duty_overload", "schedule": schedule_name},
    ))
    logger.info("[Duty] Ueberlast-Meldung fuer %s (%s uebersprungen)", agent.id, schedule_name)
    return True


async def escalate_silence(db: AsyncSession, redis, agent: Agent) -> bool:
    """Rueckfragen verhallen: nach zwei unbeantworteten Meldungen eine Stufe hoeher.

    „Unbeantwortet" heisst hier: die Benachrichtigung ist aelter als zwoelf Stunden und
    immer noch ungelesen. Das ist das einzige Signal, das wir ohne Zusatzaufwand haben —
    und es ist genau das, was ein Mensch auch pruefen wuerde.
    """
    cutoff = datetime.now(timezone.utc) - UNANSWERED_AFTER
    unanswered = (await db.execute(
        select(Notification).where(
            Notification.agent_id == agent.id,
            Notification.read.is_(False),
            Notification.created_at < cutoff,
        ).order_by(Notification.created_at)
    )).scalars().all()
    if len(unanswered) < duty_core.ESCALATE_AFTER_UNANSWERED:
        return False

    key = f"duty:escalation:{agent.id}"
    try:
        if not await redis.client.set(key, "1", nx=True, ex=HANDOVER_COOLDOWN_SECONDS):
            return False
    except Exception:  # noqa: BLE001
        logger.debug("[Duty] Eskalations-Drossel nicht verfuegbar", exc_info=True)

    lead = await team_lead_for(db, agent.id)
    stufe = "den Team-Lead" if lead else "die Administration"
    db.add(Notification(
        # Meldung haengt am Lead, wenn es einen gibt — sonst am Agenten selbst, damit sie
        # in der Admin-Ansicht auftaucht.
        agent_id=lead or agent.id,
        type="warning",
        title=f"{agent.name} wartet seit über 12 Stunden auf eine Antwort",
        message=(
            f"{len(unanswered)} Rückfragen von {agent.name} sind unbeantwortet. "
            f"Deshalb geht es jetzt an {stufe}. Älteste Frage: "
            f"„{(unanswered[0].title or '')[:80]}“."
        )[:240],
        priority="high",
        action_url=f"/agents/{agent.id}",
        meta={"reason": "duty_silence", "unanswered": len(unanswered),
              "escalated_to": lead or "admin"},
    ))
    logger.info("[Duty] Eskalation fuer %s an %s (%d unbeantwortet)",
                agent.id, lead or "admin", len(unanswered))
    return True
