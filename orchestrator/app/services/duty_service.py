"""Was passiert, wenn ein Agent ausfaellt oder ein Mensch nicht antwortet.

Eine Kette fuer beide Faelle (siehe ``core/agent_duty``):
  * Agent faellt aus  → Vertreter uebernimmt seine offenen Todos, Besitzer wird informiert.
  * Mensch schweigt   → nach zwei unbeantworteten Rueckfragen geht es eine Stufe hoeher.

Beides nutzt, was schon da ist: den Watchdog fuer haengende Aufgaben, die bestehende
Todo-Tabelle fuer die Uebergabe, ``Notification`` fuer die Meldung und ``teams`` fuer den
Lead. Kein zweites Aufgabensystem, keine zweite Meldeschiene.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import agent_duty as duty_core
from app.core.log_redaction import scrub_log
from app.models.agent import Agent
from app.models.agent_todo import AgentTodo, TodoStatus
from app.models.notification import Notification
from app.services.watchdog import md_escape

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
# Eine Ausfall-Meldung pro ZEITPLAN und Stunde. Bewusst NICHT die 12h-Drossel von
# HANDOVER_COOLDOWN_SECONDS: die zaehlt pro Agent und schluckt damit den Ausfall eines
# taeglichen Jobs komplett, sobald derselbe Agent aus irgendeinem anderen Grund schon
# einmal gemeldet wurde (#632).
SKIPPED_RUN_ALERT_COOLDOWN_SECONDS = 3600
# So lange bleibt der Merker fuer einen bereits verbuchten Slot stehen. Der DOWN-Zweig
# rueckt next_run_at nicht vor, laeuft also bei jedem Scheduler-Tick erneut in dieselbe
# Stelle — ohne Merker entstuenden pro ausgefallenem Slot hunderte Fehl-Eintraege.
SKIPPED_RUN_SLOT_TTL_SECONDS = 48 * 3600


async def _publish_telegram(redis, title: str, message: str) -> None:
    """Push a high-priority escalation straight to the Telegram alert channel.

    ``escalate_failure``/``escalate_overload``/``escalate_silence`` write their
    ``Notification`` row directly via ``db.add`` — they never go through the
    agent-facing ``POST /notifications/`` API, which is the ONLY place that turns
    ``priority in ("high", "urgent")`` into a Telegram send (app/api/notifications.py).
    A DB-only Notification with priority="high" therefore silently never reaches
    Telegram; the operator only sees it if they happen to open the Web UI. Root-caused
    via #610 (the #606/#605 overload fix looked complete because the Notification row
    existed, but nothing ever published it). Same channel + fail-silent pattern as
    ``task_router._alert_schedule_failure``.
    """
    try:
        if not redis or not redis.client:
            return
        await redis.client.publish(
            "telegram:notification",
            json.dumps({
                "text": f"⚠️ *{md_escape(title)}*\n{md_escape(message)}",
                "parse_mode": "Markdown",
            }),
        )
    except Exception:  # noqa: BLE001 — ein verpasster Alert darf die Eskalation nie zum Absturz bringen
        logger.debug("[Duty] Telegram-Publish fehlgeschlagen", exc_info=True)


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


async def escalate_failure(db: AsyncSession, redis, agent: Agent, duty: dict,
                           lost_run: str = "") -> dict:
    """Ausfall behandeln: Vertreter suchen, Arbeit uebergeben, EINE Meldung absetzen.

    Gibt ``{"handled": bool, "deputy": id|"", "todos": int}`` zurueck. Gedrosselt, damit
    ein tagelang toter Agent nicht jeden Tick erneut uebergibt.

    ``lost_run`` ist der Name des faelligen Zeitplans, falls dieser Ausfall gerade einen
    Lauf gekostet hat. Ohne ihn meldete der Fall "kein offenes Todo" woertlich *"es geht
    also nichts verloren"* — was genau dann falsch ist, wenn es etwas gekostet hat (#632).
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
    elif lost_run:
        title = f"{agent.name} ist ausgefallen — '{lost_run}' faellt aus"
        message = (
            f"{reason}. Der faellige Lauf von '{lost_run}' wurde uebersprungen und ist "
            f"fuer heute weg — es gab keinen Vertreter, an den er haette gehen koennen."
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
    if priority in ("high", "urgent"):
        await _publish_telegram(redis, title, message)
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

    title = f"{agent.name} ist ueberlastet — Zeitplan uebersprungen"[:200]
    message = (
        f"'{schedule_name}' wurde nicht ausgefuehrt: {duty.get('reason')}. Der Zeitplan "
        f"laeuft normal weiter, aber dieser Durchgang faellt aus."
    )[:240]
    db.add(Notification(
        agent_id=agent.id,
        type="warning",
        title=title,
        message=message,
        # War "normal" — landete damit nur im Web-UI-Notification-Center und nie in
        # Telegram (siehe #610), fuer einen taeglichen Job wie den Podcast reicht das
        # nicht. "high" passt zur ebenfalls hoch eingestuften escalate_failure.
        priority="high",
        action_url=f"/agents/{agent.id}",
        meta={"reason": "duty_overload", "schedule": schedule_name},
    ))
    await _publish_telegram(redis, title, message)
    logger.info("[Duty] Ueberlast-Meldung fuer %s (%s uebersprungen)", agent.id, schedule_name)
    return True


async def escalate_skipped_run(db: AsyncSession, redis, agent: Agent, duty: dict, *,
                               schedule_id: str, schedule_name: str,
                               slot: datetime) -> bool:
    """Einen wegen Ausfall uebersprungenen Zeitplan-Lauf SICHTBAR machen (#632).

    Bisher kehrte der DOWN-Zweig des Schedulers zurueck, bevor ueberhaupt ein Task
    entstand. Ein ausgefallener Lauf hinterliess damit gar nichts: keinen `failed`,
    keinen `pending`, keine Zeile in irgendeiner Liste — aus Sicht des Nutzers *und des
    Agenten selbst* hat er nie stattgefunden. Genau daran ist ein taeglicher Job
    wochenlang unbemerkt an einem Drittel der Tage ausgefallen.

    Deshalb entsteht hier ein Task mit Status ``failed``: er ist die Spur, die man
    spaeter noch finden kann. Er wird NICHT eingereiht (der Agent laeuft ja nicht) —
    er wird nur verbucht.

    Zwei Merker, weil zwei verschiedene Dinge gedrosselt gehoeren: der Eintrag genau
    einmal pro verpasstem Slot, die Meldung hoechstens einmal pro Stunde.
    """
    from app.core.task_router import _make_task_id
    from app.models.task import Task, TaskPriority, TaskStatus

    slot_iso = slot.isoformat()
    client = getattr(redis, "client", None)
    if client is not None:
        try:
            fresh = await client.set(
                f"duty:skipped:slot:{schedule_id}:{slot_iso}", "1",
                nx=True, ex=SKIPPED_RUN_SLOT_TTL_SECONDS,
            )
        except Exception:  # noqa: BLE001
            logger.debug("[Duty] Slot-Merker nicht verfuegbar", exc_info=True)
            fresh = True
        if not fresh:
            return False

    reason = duty.get("reason") or "ausgefallen"
    db.add(Task(
        id=_make_task_id(),
        title=f"[Ausgefallen] {schedule_name}"[:200],
        prompt=(
            f"Zeitplan '{schedule_name}' war um {slot_iso} faellig, wurde aber nicht "
            f"ausgefuehrt: {reason}."
        ),
        status=TaskStatus.FAILED,
        priority=int(TaskPriority.NORMAL),
        agent_id=agent.id,
        error=f"Uebersprungen — Agent {duty.get('state')}: {reason}",
        completed_at=datetime.now(timezone.utc),
        metadata_={
            "reason": "schedule_skipped",
            "schedule_id": schedule_id,
            "duty_state": duty.get("state"),
            "slot": slot_iso,
        },
    ))
    await db.flush()
    logger.warning(
        "[Duty] %s: Lauf %s ausgefallen (Agent %s: %s)",
        schedule_name, slot_iso, agent.id, duty.get("state"),
    )

    if client is not None:
        try:
            if not await client.set(
                f"duty:skipped:{schedule_id}", "1",
                nx=True, ex=SKIPPED_RUN_ALERT_COOLDOWN_SECONDS,
            ):
                return True
        except Exception:  # noqa: BLE001
            logger.debug("[Duty] Ausfall-Drossel nicht verfuegbar", exc_info=True)

    title = f"'{schedule_name}' ist ausgefallen"[:200]
    message = (
        f"Der Lauf um {slot_iso} wurde uebersprungen: {agent.name} ist "
        f"{duty.get('state')} ({reason}). Der Zeitplan laeuft weiter, dieser Durchgang "
        f"ist weg."
    )[:240]
    db.add(Notification(
        agent_id=agent.id,
        type="error",
        title=title,
        message=message,
        priority="high",
        action_url=f"/agents/{agent.id}?tab=schedules",
        meta={"reason": "duty_skipped_run", "schedule": schedule_name,
              "schedule_id": schedule_id, "slot": slot_iso},
    ))
    await _publish_telegram(redis, title, message)
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
    title = f"{agent.name} wartet seit über 12 Stunden auf eine Antwort"
    message = (
        f"{len(unanswered)} Rückfragen von {agent.name} sind unbeantwortet. "
        f"Deshalb geht es jetzt an {stufe}. Älteste Frage: "
        f"„{(unanswered[0].title or '')[:80]}“."
    )[:240]
    db.add(Notification(
        # Meldung haengt am Lead, wenn es einen gibt — sonst am Agenten selbst, damit sie
        # in der Admin-Ansicht auftaucht.
        agent_id=lead or agent.id,
        type="warning",
        title=title,
        message=message,
        priority="high",
        action_url=f"/agents/{agent.id}",
        meta={"reason": "duty_silence", "unanswered": len(unanswered),
              "escalated_to": lead or "admin"},
    ))
    await _publish_telegram(redis, title, message)
    logger.info("[Duty] Eskalation fuer %s an %s (%d unbeantwortet)",
                agent.id, lead or "admin", len(unanswered))
    return True
