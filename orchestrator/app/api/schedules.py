import os
import uuid
from datetime import datetime, timedelta, timezone

from app.core.adaptiver_takt import (
    FREMDGETAKTET_GRUND,
    LEER_SCHWELLE_STANDARD,
    fremdgetaktet,
)
from app.services.scheduler_service import _calc_next_run

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import get_redis_service, is_agent_principal, require_auth, require_auth_or_agent
from app.models.schedule import Schedule
from app.services.redis_service import RedisService
from app.schemas.schedule import (
    ReportRunRequest,
    ReportRunResponse,
    ScheduleCreate,
    ScheduleListResponse,
    ScheduleResponse,
    ScheduleTimingParseRequest,
    ScheduleTimingParseResponse,
    ScheduleUpdate,
)

router = APIRouter(prefix="/schedules", tags=["schedules"])


async def _get_user_agent_ids(user, db: AsyncSession) -> list[str] | None:
    """Return agent IDs owned by user, or None if admin (sees all).

    When called by an agent token, returns only that agent's
    own ID — the user.id is the agent_id, not a user UUID in this case.

    Ownerless agents are NOT auto-included (changed 2026-08-27, see
    tasks.py::_get_user_agent_ids) — an unassigned agent's schedules used
    to show up for every user, not just admins. is_platform_agent (explicit
    admin flag) still grants that, deliberately marked ones only.
    """
    from app.models.user import UserRole
    if hasattr(user, "role") and user.role == UserRole.ADMIN:
        return None
    if is_agent_principal(user):
        return [user.id]
    from app.models.agent import Agent
    from app.models.agent_access import AgentAccess
    owned = await db.execute(
        select(Agent.id).where(
            (Agent.user_id == user.id) | (Agent.is_platform_agent.is_(True))
        )
    )
    shared = await db.execute(
        select(AgentAccess.agent_id).where(AgentAccess.user_id == user.id)
    )
    return list({row[0] for row in owned.all()} | {row[0] for row in shared.all()})


async def _get_schedule(db: AsyncSession, schedule_id: str) -> Schedule:
    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=404, detail="Zeitplan nicht gefunden")
    return schedule


def _eigener_ohne_agent(schedule: Schedule, user) -> bool:
    """Ein Zeitplan ohne Agent gehoert dem Menschen, der ihn angelegt hat (#901).

    Ohne diese Regel war ein solcher Zeitplan fuer sein Mitglied unsichtbar —
    weder pausier- noch loeschbar. Agenten-Token zaehlen nie als Urheber.
    """
    return (
        schedule.agent_id is None
        and schedule.created_by is not None
        and not is_agent_principal(user)
        and schedule.created_by == getattr(user, "id", None)
    )


async def _check_schedule_access(schedule: Schedule, user, db: AsyncSession) -> None:
    """Raise 403 if the calling user does not own the schedule's agent."""
    from app.models.user import UserRole
    if hasattr(user, "role") and user.role == UserRole.ADMIN:
        return
    if _eigener_ohne_agent(schedule, user):
        return
    allowed = await _get_user_agent_ids(user, db)
    if allowed is not None and schedule.agent_id not in allowed:
        raise HTTPException(status_code=403, detail="Kein Zugriff auf diesen Zeitplan")


async def kandidaten_fuer_urheber(db: AsyncSession, created_by: str | None) -> set[str] | None:
    """Agenten, unter denen ein Zeitplan OHNE Agent beim Feuern waehlen darf.

    Dieselbe Grenze wie fuer einen Auftrag des Urhebers (``_get_user_agent_ids``):
    ``None`` = keine Grenze (Administrator). Bisher waehlte der Lastverteiler unter
    ALLEN Agenten der Anlage — der Zeitplan eines Mitglieds konnte auf dem Agenten
    eines anderen Nutzers laufen (#901).

    ``LookupError``, wenn kein Urheber bekannt ist (Altbestand vor #901) oder sein
    Konto nicht mehr existiert: dann darf der Zeitplan nirgends laufen.
    """
    if not created_by:
        raise LookupError("Zeitplan ohne Agent und ohne bekannten Urheber")
    from app.models.user import User
    urheber = await db.get(User, created_by)
    if urheber is None:
        raise LookupError(f"Urheber {created_by} existiert nicht mehr")
    erlaubt = await _get_user_agent_ids(urheber, db)
    return None if erlaubt is None else set(erlaubt)


_OHNE_EIGENEN_AGENTEN = (
    "Du hast noch keinen eigenen Agenten. Ein Zeitplan braucht einen "
    "Agenten, der ihn ausführt — lege zuerst einen Agenten an."
)


async def _automatisch_waehlen(user, erreichbar: set[str], priority: int, redis, db: AsyncSession) -> str:
    """„Automatisch zuweisen" eines Nicht-Administrators — schon beim Anlegen.

    Gewaehlt wird ausschliesslich unter den erreichbaren Agenten, die EIGENEN
    zuerst: der Lastverteiler (freiester laufender) erst unter den eigenen, dann
    unter allen erreichbaren; sind alle gestoppt (der Scheduler weckt ihn zum
    Lauf), der zuerst angelegte eigene. So steht am Zeitplan immer ein Agent:
    das Mitglied sieht, wer ihn ausfuehrt, und kann ihn pausieren und loeschen (#901).
    """
    from app.core.load_balancer import LoadBalancer
    from app.models.agent import Agent

    agenten = (await db.execute(
        select(Agent.id, Agent.user_id).where(Agent.id.in_(erreichbar)).order_by(Agent.created_at)
    )).all() if erreichbar else []
    if not agenten:
        raise HTTPException(status_code=400, detail=_OHNE_EIGENEN_AGENTEN)
    eigene = [a for a, besitzer in agenten if besitzer == user.id]
    alle = [a for a, _ in agenten]
    lastverteiler = LoadBalancer(redis)
    for kreis in (set(eigene), set(alle)):
        if kreis:
            gewaehlt = await lastverteiler.select_agent(priority=priority, kandidaten=kreis)
            if gewaehlt in kreis:
                return gewaehlt
    return (eigene or alle)[0]


@router.get("/", response_model=ScheduleListResponse)
async def list_schedules(user=Depends(require_auth_or_agent), db: AsyncSession = Depends(get_db)):
    query = select(Schedule).order_by(Schedule.created_at.desc())
    if hasattr(user, "role"):
        allowed = await _get_user_agent_ids(user, db)
        if allowed is not None:
            sichtbar = Schedule.agent_id.in_(allowed)
            if not is_agent_principal(user):
                # Eigene Zeitplaene ohne Agent (#901) — siehe _eigener_ohne_agent.
                sichtbar = sichtbar | (Schedule.agent_id.is_(None) & (Schedule.created_by == user.id))
            query = query.where(sichtbar)
    result = await db.execute(query)
    schedules = list(result.scalars().all())
    return ScheduleListResponse(
        schedules=[ScheduleResponse.from_schedule(s) for s in schedules],
        total=len(schedules),
    )


@router.post("/parse-timing", response_model=ScheduleTimingParseResponse)
async def parse_schedule_timing(
    body: ScheduleTimingParseRequest, user=Depends(require_auth_or_agent), db: AsyncSession = Depends(get_db),
):
    """Uebersetzt einen freien Zeitsatz ("jeden Montag um 9 Uhr") in einen
    cron_expression (Issue #196) — reine Vorschau, legt nichts an. Der
    Aufrufer uebernimmt den zurueckgegebenen cron_expression danach ganz
    normal in ``POST /schedules/``, damit es weiterhin nur einen Weg gibt,
    wie ein Zeitplan tatsaechlich entsteht.
    """
    from app.core.plan_rhythm import timezone_name
    from app.services.schedule_nl_parser import parse_natural_language_schedule

    tz_name = (body.timezone or "").strip()
    if not tz_name and body.agent_id:
        from app.models.agent import Agent as _Agent
        _agent = (await db.execute(
            select(_Agent).where(_Agent.id == body.agent_id)
        )).scalar_one_or_none()
        tz_name = timezone_name(getattr(_agent, "config", None))
    tz_name = tz_name or "UTC"

    try:
        result = await parse_natural_language_schedule(body.text)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    from app.models.schedule import Schedule as _ScheduleModel
    from app.services.scheduler_service import schedule_occurrences
    vorschau = _ScheduleModel(cron_expression=result["cron_expression"], timezone=tz_name, interval_seconds=0)
    now = datetime.now(timezone.utc)
    naechste = schedule_occurrences(vorschau, now, now + timedelta(days=35))[:3]

    return ScheduleTimingParseResponse(
        cron_expression=result["cron_expression"],
        timezone=tz_name,
        explanation=result["explanation"],
        source=result["source"],
        next_runs=naechste,
    )


@router.post("/", response_model=ScheduleResponse, status_code=201)
async def create_schedule(
    data: ScheduleCreate,
    user=Depends(require_auth_or_agent),
    db: AsyncSession = Depends(get_db),
    redis: RedisService = Depends(get_redis_service),
):
    schedule_id = uuid.uuid4().hex[:8]
    now = datetime.now(timezone.utc)

    # Self-scheduling: when an agent creates a schedule without naming a target, it
    # schedules ITSELF (the agent principal's id is the agent_id).
    agent_id = data.agent_id
    if agent_id is None and is_agent_principal(user):
        agent_id = user.id

    # Der Zielagent muss zu denen des Aufrufers gehoeren: sonst liesse sich ein
    # wiederkehrender Auftrag auf den Agenten eines anderen Nutzers legen.
    from app.api.tasks import _erreichbare_agenten, _pruefe_zielagent
    erreichbar = await _erreichbare_agenten(user, db)
    _pruefe_zielagent(agent_id, erreichbar)

    # „Automatisch zuweisen" eines Nicht-Administrators wird SOFORT aufgeloest —
    # unter seinen eigenen Agenten (#901). Vorher entstand der Zeitplan mit
    # agent_id NULL: fuer das Mitglied unsichtbar, und beim Feuern waehlte der
    # Lastverteiler unter allen Agenten der Anlage.
    if agent_id is None and erreichbar is not None:
        agent_id = await _automatisch_waehlen(user, erreichbar, data.priority, redis, db)

    # Zeitzone: was der Agent NICHT angibt, meint seine eigene. Ein Zeitplan
    # „täglich 07:00", der in UTC gerechnet wird, feuert in Berlin um neun — genau
    # so standen im Kalender Namen, die nicht zur Uhrzeit passten.
    tz_name = (data.timezone or "").strip()
    if not tz_name and agent_id:
        from app.core.plan_rhythm import timezone_name
        from app.models.agent import Agent as _Agent
        _agent = (await db.execute(
            select(_Agent).where(_Agent.id == agent_id)
        )).scalar_one_or_none()
        tz_name = timezone_name(getattr(_agent, "config", None))
    data.timezone = tz_name or "UTC"

    # One-shot ("look at this again in N seconds") fires once at now+run_in_seconds;
    # interval_seconds==0 + no cron → the scheduler disables it after the single run.
    if data.run_in_seconds is not None:
        next_run_at = now + timedelta(seconds=data.run_in_seconds)
    else:
        next_run_at = _calc_next_run(data, now)  # type: ignore[arg-type]

    # Legt ein Agent den Zeitplan im Gespraech an, gehoeren die Laeufe dorthin:
    # jeder erscheint in diesem Chat als Kachel mit Ergebnis. Fuer alle
    # Laufzeiten gleich, weil hier und nicht im Werkzeug entschieden.
    chat_session_id = None
    if is_agent_principal(user) and agent_id == user.id:
        from app.core.task_router import faden_des_laufenden_zuges
        chat_session_id = await faden_des_laufenden_zuges(redis, agent_id)

    # Adaptiver Takt (#941): Der Grundtakt ist das Intervall, mit dem der
    # Zeitplan angelegt wurde — danach taktet ``report_run`` ihn selbst. Ohne
    # ``adaptive`` bleiben alle Felder auf ihrem Standard, und der Zeitplan
    # verhaelt sich wie bisher.
    if data.adaptive and fremdgetaktet(data.name):
        raise HTTPException(status_code=400, detail=FREMDGETAKTET_GRUND)
    basis_intervall_s = data.basis_intervall_s or (data.interval_seconds if data.adaptive else None)
    stufen = list(data.stufen) if data.stufen else None

    schedule = Schedule(
        id=schedule_id,
        name=data.name,
        prompt=data.prompt,
        chat_session_id=chat_session_id,
        interval_seconds=data.interval_seconds,
        adaptive=bool(data.adaptive),
        basis_intervall_s=basis_intervall_s,
        stufen=stufen,
        leer_schwelle=data.leer_schwelle or LEER_SCHWELLE_STANDARD,
        cron_expression=data.cron_expression,
        timezone=data.timezone,
        priority=data.priority,
        agent_id=agent_id,
        model=data.model,
        created_by=None if is_agent_principal(user) else getattr(user, "id", None),
        enabled=True,
        next_run_at=next_run_at,
        total_runs=0,
        success_count=0,
        fail_count=0,
    )
    db.add(schedule)
    await db.commit()
    await db.refresh(schedule)
    return ScheduleResponse.from_schedule(schedule)


@router.post("/report-run", response_model=ReportRunResponse)
async def report_run(
    body: ReportRunRequest,
    user=Depends(require_auth_or_agent),
    db: AsyncSession = Depends(get_db),
):
    """Das Ergebnis des LAUFENDEN Zeitplan-Laufs melden und den Takt anpassen (#941).

    Der Aufrufer nennt keine Zeitplan-Kennung: Zugeordnet wird ueber den
    laufenden Auftrag, der die Kennung seines Zeitplans in den Metadaten traegt.
    Damit ist die Meldung auch nicht missbrauchbar — ein Agent ohne laufenden
    Zeitplan-Auftrag kann keinen Takt veraendern, und der Zeitplan muss
    zusaetzlich die normale Zugriffspruefung bestehen.

    Ohne zugehoerigen Zeitplan-Lauf gibt es 409 mit Begruendung und ausdruecklich
    KEIN stilles Nichts (#941, Abnahme).
    """
    from app.services.takt_service import KeinZeitplanLauf, lauf_melden, zeitplan_des_laufs

    # Ein Agenten-Token meldet fuer sich selbst. Ein Mensch (oder ein
    # Administrator, der nachhilft) muss den Auftrag nennen — sonst waere nicht
    # entscheidbar, welcher Lauf gemeint ist.
    agent_id = user.id if is_agent_principal(user) else None
    if agent_id is None:
        if not body.task_id:
            raise HTTPException(
                status_code=400,
                detail="Ohne Agenten-Token bitte task_id des Zeitplan-Laufs angeben.",
            )
        from app.models.task import Task as _Task
        task = await db.get(_Task, body.task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Auftrag nicht gefunden")
        from app.api.tasks import _erreichbare_agenten
        erreichbar = await _erreichbare_agenten(user, db)
        if erreichbar is not None and task.agent_id not in erreichbar:
            raise HTTPException(status_code=403, detail="Kein Zugriff auf diesen Auftrag")
        agent_id = task.agent_id

    try:
        schedule, task = await zeitplan_des_laufs(db, agent_id, body.task_id)
    except KeinZeitplanLauf as fehler:
        raise HTTPException(status_code=409, detail=str(fehler))

    # Derselbe Zugriffsschutz wie bei jedem anderen Zugriff auf einen Zeitplan:
    # ein fremder Zeitplan wird abgewiesen, auch wenn der Auftrag passt.
    await _check_schedule_access(schedule, user, db)

    wechsel = await lauf_melden(db, schedule, task, body.outcome)
    return ReportRunResponse(
        schedule_id=schedule.id,
        name=schedule.name,
        outcome=body.outcome,
        adaptive=bool(wechsel.get("adaptive")),
        geaendert=bool(wechsel.get("geaendert")),
        intervall_vorher_s=int(wechsel.get("intervall_vorher_s") or 0),
        intervall_s=int(wechsel.get("intervall_s") or 0),
        leerlaeufe=int(wechsel.get("leerlaeufe") or 0),
        stufe=int(wechsel.get("stufe") or 0),
        next_run_at=schedule.next_run_at,
        takt=wechsel.get("takt") or "",
        hinweis=wechsel.get("hinweis") or "",
    )


@router.get("/{schedule_id}", response_model=ScheduleResponse)
async def get_schedule(schedule_id: str, user=Depends(require_auth_or_agent), db: AsyncSession = Depends(get_db)):
    schedule = await _get_schedule(db, schedule_id)
    await _check_schedule_access(schedule, user, db)
    return ScheduleResponse.from_schedule(schedule)


@router.put("/{schedule_id}", response_model=ScheduleResponse)
async def update_schedule(
    schedule_id: str, data: ScheduleUpdate, user=Depends(require_auth_or_agent), db: AsyncSession = Depends(get_db),
):
    schedule = await _get_schedule(db, schedule_id)
    await _check_schedule_access(schedule, user, db)
    # Geprueft wurde bisher nur der ALTE Zeitplan — er liess sich danach auf
    # einen fremden Agenten umhaengen.
    if data.agent_id is not None:
        from app.api.tasks import _erreichbare_agenten, _pruefe_zielagent
        _pruefe_zielagent(data.agent_id, await _erreichbare_agenten(user, db))
    # Den Agenten wieder abnehmen („Automatisch") darf nur ein Administrator —
    # sonst entstuende genau der verwaiste Zeitplan aus #901 auf Umwegen.
    elif "agent_id" in data.model_fields_set and schedule.agent_id is not None:
        from app.core.ownership import is_admin
        if not is_admin(user):
            raise HTTPException(status_code=400, detail="Ein Zeitplan braucht einen Agenten, der ihn ausführt.")

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(schedule, field, value)

    # Adaptiver Takt (#941): Nur fuer Intervall-Zeitplaene — bei Cron ist die
    # Taktrechnung mehrdeutig. Geprueft wird der Zeitplan NACH den Aenderungen
    # dieses Aufrufs, sonst liesse sich adaptiv und cron im selben Aufruf setzen.
    if schedule.adaptive and (schedule.cron_expression or not schedule.interval_seconds):
        raise HTTPException(
            status_code=400,
            detail=("Adaptiver Takt gilt nur für Intervall-Zeitpläne: Dieser Zeitplan "
                    "läuft nach Cron (oder hat kein Intervall)."),
        )
    # Und nicht auf Zeitplaenen, deren Takt die Plattform selbst fuehrt (#941,
    # Punkt 4) — sonst ueberschreiben sich adaptiver Takt und Abgleich
    # gegenseitig, und der Nutzer sieht einen Zeitplan, der ohne Grund springt.
    if schedule.adaptive and fremdgetaktet(schedule.name):
        raise HTTPException(status_code=400, detail=FREMDGETAKTET_GRUND)
    # ── Grundtakt und Zustand (#941) ────────────────────────────────────────
    # Der Grundtakt ist der Takt, auf den ein Lauf mit Arbeit zurueckstellt.
    # Vier Faelle, bewusst in dieser Reihenfolge und sich gegenseitig
    # ausschliessend — eine spaetere Zeile darf keine fruehere still
    # zurueckdrehen (genau das war Punkt 2 des Reviews).
    if data.adaptive is False:
        # Ausschalten: Der Zustand bleibt stehen (er erklaert die Historie),
        # das Intervall faellt auf den Grundtakt zurueck — sonst bliebe der
        # Zeitplan fuer immer im 8-Stunden-Leerlauftakt haengen. Kommt im
        # SELBEN Aufruf ein neues Intervall mit, gilt dieses: Wer beides setzt,
        # meint das neue Intervall, nicht den alten Grundtakt.
        if schedule.basis_intervall_s and data.interval_seconds is None:
            schedule.interval_seconds = schedule.basis_intervall_s
        schedule.leerlaeufe = 0
        schedule.stufe = 0
    elif data.basis_intervall_s is not None and schedule.adaptive:
        # Grundtakt ausdruecklich gesetzt: Er gilt ab sofort auch als laufender
        # Takt, sonst zeigte die Ansicht weiter die alte Stufe.
        schedule.interval_seconds = schedule.basis_intervall_s
        schedule.leerlaeufe = 0
        schedule.stufe = 0
    elif data.interval_seconds is not None and schedule.adaptive:
        # Wer das Intervall eines adaptiven Zeitplans von Hand korrigiert, meint
        # den GRUNDTAKT — nicht die Stufe, auf der er gerade steht. Ohne diese
        # Zeile holte der naechste Lauf den alten Grundtakt zurueck, und ein
        # Zeitplan auf der letzten Stufe sprang nach EINEM leeren Lauf sofort
        # wieder auf 8 Std: die Korrektur waere still verpufft (#941, Punkt 2).
        # Der Zaehler faengt neu an, denn die alte Leerlauf-Strecke gehoert zum
        # alten Takt.
        schedule.basis_intervall_s = schedule.interval_seconds
        schedule.leerlaeufe = 0
        schedule.stufe = 0
    elif data.adaptive is True and schedule.basis_intervall_s is None:
        # Einschalten ohne eigene Angabe: das aktuelle Intervall ist der Grundtakt.
        schedule.basis_intervall_s = schedule.interval_seconds

    # Recalculate next_run if timing or timezone changed
    if (data.interval_seconds is not None
            or data.cron_expression is not None
            or data.timezone is not None
            or data.adaptive is False
            or data.basis_intervall_s is not None
            or data.enabled is True):
        now = datetime.now(timezone.utc)
        schedule.next_run_at = _calc_next_run(schedule, now)

    await db.commit()
    await db.refresh(schedule)
    return ScheduleResponse.from_schedule(schedule)


@router.delete("/{schedule_id}")
async def delete_schedule(schedule_id: str, user=Depends(require_auth_or_agent), db: AsyncSession = Depends(get_db)):
    schedule = await _get_schedule(db, schedule_id)
    await _check_schedule_access(schedule, user, db)
    await db.delete(schedule)
    await db.commit()
    return {"status": "deleted", "schedule_id": schedule_id}


@router.post("/{schedule_id}/trigger")
async def trigger_schedule(schedule_id: str, user=Depends(require_auth_or_agent), db: AsyncSession = Depends(get_db)):
    """Manually trigger a schedule to run immediately."""
    from app.core.agent_manager import PROACTIVE_PROMPT
    from app.core.load_balancer import LoadBalancer
    from app.core.task_router import TaskRouter
    from app.services.redis_service import RedisService

    schedule = await _get_schedule(db, schedule_id)
    await _check_schedule_access(schedule, user, db)

    redis = RedisService(os.environ.get("REDIS_URL", "redis://redis:6379"))
    await redis.connect()
    try:
        lb = LoadBalancer(redis)
        router = TaskRouter(db, redis, lb)

        is_proactive = schedule.name.startswith("[Proactive]")
        prompt = PROACTIVE_PROMPT if is_proactive else schedule.prompt

        # Ohne Agent waehlt der Lastverteiler — nur unter den Agenten des Ausloesenden.
        from app.api.tasks import _erreichbare_agenten
        task = await router.create_and_route_task(
            title=f"[Manual] {schedule.name}",
            prompt=prompt,
            priority=schedule.priority,
            agent_id=schedule.agent_id,
            model=schedule.model,
            metadata={"schedule_id": schedule.id, "manual_trigger": True},
            erlaubte_agenten=await _erreichbare_agenten(user, db),
        )

        now = datetime.now(timezone.utc)
        schedule.last_run_at = now
        schedule.total_runs += 1
        await db.commit()

        return {"status": "triggered", "schedule_id": schedule_id, "task_id": task.id}
    finally:
        await redis.disconnect()


@router.post("/{schedule_id}/pause")
async def pause_schedule(schedule_id: str, user=Depends(require_auth_or_agent), db: AsyncSession = Depends(get_db)):
    schedule = await _get_schedule(db, schedule_id)
    await _check_schedule_access(schedule, user, db)
    schedule.enabled = False
    await db.commit()
    return {"status": "paused", "schedule_id": schedule_id}


@router.post("/{schedule_id}/resume")
async def resume_schedule(schedule_id: str, user=Depends(require_auth_or_agent), db: AsyncSession = Depends(get_db)):
    schedule = await _get_schedule(db, schedule_id)
    await _check_schedule_access(schedule, user, db)
    schedule.enabled = True
    now = datetime.now(timezone.utc)
    schedule.next_run_at = _calc_next_run(schedule, now)
    await db.commit()
    return {"status": "resumed", "schedule_id": schedule_id}
