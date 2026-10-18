"""Watchdog detection helpers (issue #211).

Pure, dependency-light detection logic for stale tasks and missed schedules,
kept out of scheduler_service so it can be unit-tested without pulling in the
docker/redis import chain. The SchedulerService owns the loop, DB sessions and
alerting; this module owns the "is it stale / missed?" decision.
"""

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

try:
    from croniter import croniter
    _CRONITER_AVAILABLE = True
except ImportError:
    _CRONITER_AVAILABLE = False

from app.models.schedule import Schedule
from app.models.task import Task, TaskStatus

# ACHTUNG, hier stand jahrelang etwas Falsches (#692): "Eine laufende Aufgabe
# schiebt updated_at bei jedem Status-/Schritt-Schreiben weiter". Fuer den
# Agenten-Pfad stimmte das nie — zwischen `task:started` und `task:completions`
# schrieb NICHTS an der Zeile. Der Waechter mass damit nicht die Gesundheit des
# Arbeiters, sondern die verstrichene Zeit: eine harte Obergrenze fuer jede
# delegierte Aufgabe, gemeldet als "Worker still gestorben". Am 31.08.2026
# starben so vier parallele Reviews nach 30.3 Minuten mitten in der Arbeit.
#
# Seitdem sendet der Task-Runner ein echtes Lebenszeichen (`task:heartbeat`,
# jede Minute), und der Waechter misst wieder, was sein Name behauptet. Der Wert
# hier ist nur noch der Rueckfall — die Anlage stellt ihn ueber
# `watchdog_stale_task_minutes` ein (Standard 180, damit ein Agent auf einem
# aelteren Abbild ohne Herzschlag nicht sofort wieder gedeckelt ist).
#
# Eine Zeitplanung, deren next_run_at aus dem Kulanzfenster gelaufen ist,
# bedeutet dagegen wirklich: der Planer war zur Feuerzeit nicht da.
_STALE_TASK_THRESHOLD = timedelta(minutes=30)
_MISSED_SCHEDULE_GRACE = timedelta(minutes=5)
# #1001: Oberhalb davon zaehlt count_silently_advanced_slots nicht mehr Slot
# fuer Slot (bei einem minuetlichen Cron sind das ~1,4 Sekunden pro Tick
# Watchdog-Blockade je totem Zeitplan -- bei mehreren toten Zeitplaenen
# gleichzeitig blockiert das den ganzen Event-Loop), sondern schaetzt ueber
# den Abstand der ersten beiden faelligen Slots. Fuer jede periodische Regel
# (die ueberwiegende Mehrheit: minuetlich/stuendlich/taeglich/...) ist das
# exakt oder nahe exakt; fuer kalendarisch ungleichmaessige Regeln (z.B.
# monatlich) bis zu ein paar Prozent daneben -- fuer Alarm-Verdopplungs-
# Buckets (grobe Zehnerpotenzen) reicht das.
_EXACT_SLOT_COUNT_LIMIT = 2_000
# Der Sentinel erneuert sein Lebenszeichen alle 15 Sekunden (sentinel_service.py).
# Zwei Minuten Toleranz heisst: acht verpasste Schlaege, bevor Alarm ausgeloest
# wird — genug fuer eine Redis-Neuverbindung, zu wenig fuer einen echten
# Stillstand, der unbemerkt bliebe.
_SENTINEL_HEARTBEAT_THRESHOLD = timedelta(minutes=2)


def is_sentinel_stale(
    last_beat: str | float | None,
    now: datetime,
    threshold: timedelta = _SENTINEL_HEARTBEAT_THRESHOLD,
) -> bool:
    """Ist das Lebenszeichen des Sentinel zu alt?

    Ein Waechter, der stehenbleibt, ist gefaehrlicher als gar keiner: die Anlage
    sieht ueberwacht aus und ist es nicht. Deshalb ist ein FEHLENDES Lebenszeichen
    kein Alarm — der Dienst ist dann schlicht ausgeschaltet, was ein bewusster
    Zustand ist. Alarm gibt es nur, wenn er einmal gelebt hat und dann verstummt.

    ``last_beat`` ist der Rohwert aus Redis (Unix-Zeit als Zeichenkette). Ein
    unlesbarer Wert gilt als still — lieber ein Fehlalarm als ein blinder Fleck.
    """
    if last_beat is None or last_beat == "":
        return False
    try:
        beat = float(last_beat)
    except (TypeError, ValueError):
        return True
    if beat <= 0:
        return True
    alter = now.timestamp() - beat
    return alter > threshold.total_seconds()


def md_escape(s: str) -> str:
    """Escape Telegram-Markdown metacharacters in a free-text value."""
    return (
        s.replace("\\", "\\\\")
        .replace("_", "\\_")
        .replace("*", "\\*")
        .replace("`", "\\`")
        .replace("[", "\\[")
    )


def as_utc(dt: datetime | None) -> datetime | None:
    """Normalise a possibly naive datetime to timezone-aware UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def docker_startzeit(roh: str | None) -> datetime | None:
    """``State.StartedAt`` eines Containers als UTC-Zeitpunkt — oder ``None``.

    Docker liefert Nanosekunden ("...58.662587871Z"); ``fromisoformat`` kann
    hoechstens Mikrosekunden. "0001-01-01..." heisst: nie gestartet.
    """
    if not roh:
        return None
    try:
        text = re.sub(r"(\.\d{6})\d+", r"\1", str(roh)).replace("Z", "+00:00")
        gestartet = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if gestartet.year < 2000:
        return None
    return as_utc(gestartet)


#: Container-Zustaende, in denen sicher KEIN Prozess mehr arbeitet. ``fehlt``
#: heisst: Docker kennt keinen Container dieses Agenten. Bewusst NICHT dabei:
#: ``paused`` (eingefroren, nicht tot) und ``unbekannt`` (Docker nicht
#: erreichbar — ein Ausfall der Abfrage ist kein Beweis fuer einen Ausfall des
#: Agenten).
_OHNE_PROZESS = frozenset({"fehlt", "exited", "dead", "created", "restarting", "removing"})


def lauf_nachweislich_weg(
    task_started_at: datetime | None,
    container_zustand: str,
    container_start: datetime | None,
) -> bool:
    """Kann der Prozess, der diese Aufgabe bearbeitet hat, noch existieren?

    ``True`` nur bei BEWEIS, dass er weg ist: der Container laeuft nicht mehr,
    oder er wurde NACH dem Start der Aufgabe (neu) gestartet — durch Neubau,
    ``docker restart`` oder die Neustart-Regel nach einem Absturz. Die Aufgabe
    steckte dann im alten Prozess.

    Alles andere zaehlt als „lebt vielleicht": nach einem reinen
    Orchestrator-Neustart laeuft der Container unveraendert weiter, und eine
    Aufgabe, die der frische Container schon aufgenommen hat, hat eine juengere
    Startzeit als er. Im Zweifel bleibt sie stehen — der Stillstands-Waechter
    faengt sie dann ueber den fehlenden Herzschlag.
    """
    if container_zustand in _OHNE_PROZESS:
        return True
    if container_zustand != "running":
        return False
    gestartet = as_utc(task_started_at)
    container = as_utc(container_start)
    if gestartet is None or container is None:
        return False
    return container > gestartet


def is_task_stale(task: Task, now: datetime, threshold: timedelta = _STALE_TASK_THRESHOLD) -> bool:
    """Eine laufende Aufgabe gilt als tot, wenn ihr letztes Lebenszeichen zu alt ist.

    Das Lebenszeichen ist `updated_at`; seit #692 schiebt der Herzschlag des
    Task-Runners diese Spalte tatsaechlich weiter.
    """
    if task.status != TaskStatus.RUNNING:
        return False
    updated = as_utc(task.updated_at)
    if updated is None:
        return False
    return (now - updated) > threshold


def is_schedule_missed(
    schedule: Schedule, now: datetime, grace: timedelta = _MISSED_SCHEDULE_GRACE
) -> bool:
    """An enabled schedule is missed when next_run_at slipped past the grace window."""
    if not schedule.enabled:
        return False
    nra = as_utc(schedule.next_run_at)
    if nra is None:
        return False
    return (now - nra) > grace


def silently_advanced_marker(schedule: Schedule) -> str:
    """Dedup-Marke eines gemeldeten Verlusts: der last_run_at, der beim
    Erkennen galt. Aendert er sich, lief der Zeitplan wieder -- ein neuer
    Verlust danach zaehlt erneut. Eine Funktion, damit Waechter und
    _report_dropped_slot dieselbe Marke setzen (#803: sonst buchte der
    Waechter jeden ordentlich gemeldeten Verlust ein zweites Mal)."""
    letzter_lauf = as_utc(schedule.last_run_at)
    return letzter_lauf.isoformat() if letzter_lauf else "never"


def _silently_advanced_bounds(
    schedule: Schedule, now: datetime, grace: timedelta
) -> tuple[datetime, datetime | None] | None:
    """Shared boundary math for `is_schedule_silently_advanced` and
    `count_silently_advanced_slots` (#1001): both need the exact same
    `letzter_faelliger_slot` and "since when do we count" starting point, or
    the two could disagree about whether/how much was lost.

    Returns `(letzter_faelliger_slot, start)` where `start` is `last_run_at`,
    or `None` if the schedule never ran but is already past its earliest
    possible due slot (`created_at` as lower bound). Returns `None` itself
    when the schedule cannot be evaluated at all (disabled, no cron support,
    broken cron rule, not yet due since creation, or a run already pending
    for the current slot) — see `is_schedule_silently_advanced` for why each
    of those is not a loss.
    """
    if not schedule.enabled or not schedule.cron_expression or not _CRONITER_AVAILABLE:
        return None
    try:
        tz_name = getattr(schedule, "timezone", None) or "UTC"
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = timezone.utc
        cron = croniter(schedule.cron_expression, now.astimezone(tz))
        naechster_slot = cron.get_next(datetime).astimezone(timezone.utc)
        cron = croniter(schedule.cron_expression, now.astimezone(tz))
        letzter_faelliger_slot = cron.get_prev(datetime).astimezone(timezone.utc)
        if (now - letzter_faelliger_slot) <= grace:
            # Juengster Slot noch in der Dispatch-Karenz -> der davor muss
            # gelaufen sein. Die Karenz gehoert auf die Jetzt-Seite; als
            # Abzug am Slot verschob sie nur die Schwelle und liess genau
            # diesen Fall taeglich fuer jeden gesunden Zeitplan anschlagen.
            letzter_faelliger_slot = cron.get_prev(datetime).astimezone(timezone.utc)
    except Exception:
        return None  # eine kaputte Cron-Regel ist Sache von _calc_next_run, nicht hier
    naechster_lauf = as_utc(getattr(schedule, "next_run_at", None))
    if naechster_lauf is not None and naechster_lauf < naechster_slot:
        # Noch nicht "advanced": der Scheduler haelt fuer diesen Slot einen
        # Termin (Retry in 12 min, oder der Slot selbst nach Stillstand).
        return None
    letzter_lauf = as_utc(schedule.last_run_at)
    if letzter_lauf is None:
        # Nie gelaufen: der rein mathematische "letzte faellige Slot laut
        # Cron-Regel" kann in der Vergangenheit liegen, obwohl der Zeitplan
        # zu dem Zeitpunkt noch gar nicht EXISTIERTE (die Regel kennt keine
        # Anlage-Historie). Existiert eine created_at, gilt sie als untere
        # Schranke — sonst meldete jeder frisch angelegte taegliche Zeitplan
        # sich faelschlich schon vor seiner allerersten Feuerung als verloren.
        erstellt = as_utc(getattr(schedule, "created_at", None))
        if erstellt is not None and erstellt > letzter_faelliger_slot:
            return None
        return (letzter_faelliger_slot, erstellt)
    if letzter_lauf >= letzter_faelliger_slot:
        return None
    return (letzter_faelliger_slot, letzter_lauf)


def is_schedule_silently_advanced(
    schedule: Schedule, now: datetime, grace: timedelta = _MISSED_SCHEDULE_GRACE
) -> bool:
    """A cron schedule that skipped a due slot without ever running for it (#720).

    `_retry_or_advance` gives up on a slot it cannot retry (no Redis, no retry
    budget) by calling `_calc_next_run`, which ALWAYS pushes `next_run_at`
    into the future — the same call a genuine, healthy run makes. Every other
    signal (`fail_count`, `success_rate`, `next_run_at` itself, `enabled`)
    therefore reads "healthy" even though a slot was dropped; `is_schedule_missed`
    above is blind to it by construction, since it only ever looks for
    `next_run_at` stuck in the PAST.

    The only tell is `last_run_at` trailing behind what the cron rule says
    should already have fired: a daily 21:00 report last seen two days ago,
    while `next_run_at` innocently points at tomorrow 21:00, proves at least
    one slot fired the cron tick and produced nothing.

    Drei Faelle sehen genauso aus, sind aber KEIN Verlust (#803):
    * Dispatch-Latenz: der Waechter-Tick laeuft in `run()` bewusst VOR
      `_check_due_schedules`; Sekunden nach der Faelligkeit steht
      `last_run_at` noch auf dem vorigen Slot. Der juengste Slot bekommt
      deshalb `grace` Zeit -- geprueft wird dann der Slot davor, damit
      Zeitplaene mit kurzer Periode (`*/5`) nicht blind werden.
    * Ein Lauf steht noch an: `next_run_at` liegt VOR dem naechsten
      regulaeren Slot (Wiederholung nach transientem Skip, Nachholen nach
      Scheduler-Stillstand -- letzteres meldet `is_schedule_missed`).
    * Der Verlust ist schon gebucht (`_report_dropped_slot`) -- das regelt
      die Marke, siehe `silently_advanced_marker`.
    """
    bounds = _silently_advanced_bounds(schedule, now, grace)
    if bounds is None:
        return False
    letzter_faelliger_slot, start = bounds
    if start is None:
        return True
    return start < letzter_faelliger_slot


def count_silently_advanced_slots(
    schedule: Schedule, now: datetime, grace: timedelta = _MISSED_SCHEDULE_GRACE
) -> int:
    """How many due cron slots has this schedule lost, not just whether (#1001).

    `is_schedule_silently_advanced` answers yes/no; that collapses a single
    blip and a schedule dead for weeks into the same signal, and — because
    the alert dedups on `last_run_at`, which a truly dead schedule never
    changes again — into literally the same ONE alert forever. This counts
    the missed slots so the caller can re-alert as the count grows (e.g. on
    every doubling) instead of going silent exactly when the outage is worst.

    Returns 0 when nothing was lost (mirrors `is_schedule_silently_advanced`
    returning False). When the schedule never ran (no `created_at` either),
    the exact count isn't derivable — returns 1 (the minimum truthful claim:
    at least one slot is late) rather than pretending to precision we don't
    have.
    """
    bounds = _silently_advanced_bounds(schedule, now, grace)
    if bounds is None:
        return 0
    # _CRONITER_AVAILABLE==False waere hier unreachable: _silently_advanced_bounds
    # prueft das bereits selbst und liefert in diesem Fall None (siehe oben).
    letzter_faelliger_slot, start = bounds
    if start is None:
        return 1
    tz_name = getattr(schedule, "timezone", None) or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc
    try:
        cron = croniter(schedule.cron_expression, start.astimezone(tz))
        erster_slot = cron.get_next(datetime).astimezone(timezone.utc)
        if erster_slot > letzter_faelliger_slot:
            return 1  # defensiv; _silently_advanced_bounds haette sonst None geliefert
        intervall_s = (erster_slot - start).total_seconds()
        gesamt_s = (letzter_faelliger_slot - start).total_seconds()
        grobe_schaetzung = int(gesamt_s / intervall_s) + 1 if intervall_s > 0 else 1
        if grobe_schaetzung > _EXACT_SLOT_COUNT_LIMIT:
            return max(grobe_schaetzung, 1)
        cron = croniter(schedule.cron_expression, start.astimezone(tz))
        count = 0
        while True:
            nxt = cron.get_next(datetime).astimezone(timezone.utc)
            if nxt > letzter_faelliger_slot:
                break
            count += 1
        return max(count, 1)
    except Exception:
        return 1


def mark_task_stale(
    task: Task, now: datetime, threshold: timedelta = _STALE_TASK_THRESHOLD
) -> Task:
    """Flip a stale task to FAILED with a diagnostic error + metadata flag.

    ``threshold`` steht in der Meldung — mit einer fest verdrahteten Zahl wuerde
    sie bei einer angehobenen Schwelle etwas Falsches behaupten und die naechste
    Fehlersuche in die Irre schicken (#692).
    """
    minutes = int(threshold.total_seconds() // 60)
    task.status = TaskStatus.FAILED
    task.completed_at = now
    task.error = f"Watchdog: no heartbeat for over {minutes} min — task marked stale."
    meta = dict(task.metadata_ or {})
    meta["stale"] = True
    meta["stale_detected_at"] = now.isoformat()
    task.metadata_ = meta
    return task


async def find_stale_tasks(
    db, now: datetime, threshold: timedelta = _STALE_TASK_THRESHOLD
) -> list[Task]:
    """Return RUNNING tasks whose heartbeat is older than the threshold."""
    cutoff = now - threshold
    result = await db.execute(
        select(Task).where(Task.status == TaskStatus.RUNNING, Task.updated_at < cutoff)
    )
    return [t for t in result.scalars().all() if is_task_stale(t, now, threshold)]


async def find_missed_schedules(
    db, now: datetime, grace: timedelta = _MISSED_SCHEDULE_GRACE
) -> list[Schedule]:
    """Return enabled schedules whose next_run_at slipped past the grace window."""
    cutoff = now - grace
    result = await db.execute(
        select(Schedule).where(
            Schedule.enabled == True,  # noqa: E712
            Schedule.next_run_at < cutoff,
        )
    )
    return [s for s in result.scalars().all() if is_schedule_missed(s, now, grace)]


async def find_silently_advanced_schedules(
    db, now: datetime, grace: timedelta = _MISSED_SCHEDULE_GRACE
) -> list[Schedule]:
    """Return enabled cron schedules that skipped a due slot without a run for
    it (#720) — see is_schedule_silently_advanced for why next_run_at alone
    (what find_missed_schedules queries on) cannot see this class at all: the
    give-up path always pushes it into the future, same as a healthy run.
    Fetches every enabled cron schedule rather than filtering in SQL, since
    the actual test needs the cron rule evaluated in Python (croniter).
    """
    result = await db.execute(
        select(Schedule).where(
            Schedule.enabled == True,  # noqa: E712
            Schedule.cron_expression.isnot(None),
        )
    )
    return [s for s in result.scalars().all() if is_schedule_silently_advanced(s, now, grace)]
