"""Watchdog detection helpers (issue #211).

Pure, dependency-light detection logic for stale tasks and missed schedules,
kept out of scheduler_service so it can be unit-tested without pulling in the
docker/redis import chain. The SchedulerService owns the loop, DB sessions and
alerting; this module owns the "is it stale / missed?" decision.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

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
