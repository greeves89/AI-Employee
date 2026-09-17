"""Watchdog detection helpers (issue #211).

Pure, dependency-light detection logic for stale tasks and missed schedules,
kept out of scheduler_service so it can be unit-tested without pulling in the
docker/redis import chain. The SchedulerService owns the loop, DB sessions and
alerting; this module owns the "is it stale / missed?" decision.
"""

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
    """
    if not schedule.enabled or not schedule.cron_expression or not _CRONITER_AVAILABLE:
        return False
    try:
        tz_name = getattr(schedule, "timezone", None) or "UTC"
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = timezone.utc
        letzter_faelliger_slot = croniter(
            schedule.cron_expression, now.astimezone(tz)
        ).get_prev(datetime).astimezone(timezone.utc)
    except Exception:
        return False  # eine kaputte Cron-Regel ist Sache von _calc_next_run, nicht hier
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
            return False
        return (now - letzter_faelliger_slot) > grace
    return letzter_lauf < (letzter_faelliger_slot - grace)


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
