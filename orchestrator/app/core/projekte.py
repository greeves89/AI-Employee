"""Projekte: Board, Kennzahlen und Abnahme eines Projekts.

Ein Projekt ist ein Team (``models/team.py``) mit Board und Raum. Die Auftraege
eines Projekts erkennt man an ``Task.team_id``; Unterauftraege erben die
Zuordnung beim Anlegen (``models/task._projekt_erben``).

Spalten des Boards:
* ``wartet``      — angelegt, noch nicht begonnen (pending/queued)
* ``in_arbeit``   — laeuft gerade
* ``zur_abnahme`` — fertig oder gescheitert, braucht den Blick des Menschen
                    (nur Auftraege, die ueber das Projekt erteilt wurden:
                    ``metadata.abnahme``; Teilarbeiten des Leads nicht)
* ``erledigt``    — abgenommen, abgebrochen oder Teilarbeit fertig

Sichtbarkeit: ein Mensch sieht nur Auftraege SEINER Agenten (``sichtbar``),
auch wenn ein fremder Auftrag faelschlich am Projekt haengt. Administratoren
uebergeben ``None`` und sehen alles.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task, TaskStatus

AKTIV = (TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.RUNNING)
ABNAHMEFAEHIG = (TaskStatus.COMPLETED, TaskStatus.FAILED)
FENSTER_ERLEDIGT = timedelta(days=30)
# Wie lange ein nicht abgenommenes Ergebnis auf dem Board wartet.
FENSTER_ABNAHME = timedelta(days=90)
MAX_ERLEDIGT = 40
VERLAUF_TAGE = 7


def _zone() -> ZoneInfo:
    from app.core.agent_duty import anlagen_zeitzone

    try:
        return ZoneInfo(anlagen_zeitzone())
    except Exception:  # noqa: BLE001
        return ZoneInfo("UTC")


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def braucht_abnahme(task: Task) -> bool:
    return bool((task.metadata_ or {}).get("abnahme"))


def spalte(task: Task) -> str:
    if task.status in AKTIV:
        return "in_arbeit" if task.status == TaskStatus.RUNNING else "wartet"
    meta = task.metadata_ or {}
    if (
        task.status in ABNAHMEFAEHIG
        and braucht_abnahme(task)
        and task.abgenommen_at is None
        and not meta.get("zurueckgegeben_als")
    ):
        return "zur_abnahme"
    return "erledigt"


def _kurz(text: str | None, n: int) -> str | None:
    if not text:
        return None
    text = text.strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def karte(task: Task) -> dict:
    meta = task.metadata_ or {}
    return {
        "id": task.id,
        "titel": task.title,
        "status": task.status.value if hasattr(task.status, "value") else str(task.status),
        "spalte": spalte(task),
        "agent_id": task.agent_id,
        "abnahme": braucht_abnahme(task),
        "teilauftrag": not braucht_abnahme(task) and bool(task.parent_task_id),
        "zurueckgegeben_als": meta.get("zurueckgegeben_als"),
        "erstellt": _utc(task.created_at),
        "gestartet": _utc(task.started_at),
        "fertig": _utc(task.completed_at),
        "abgenommen": _utc(task.abgenommen_at),
        "kosten_usd": task.cost_usd,
        "ergebnis": _kurz(task.result, 280),
        "fehler": _kurz(task.error, 200),
    }


async def auftraege(
    db: AsyncSession, team_id: str, sichtbar: list[str] | None, *, jetzt: datetime | None = None
) -> list[Task]:
    """Die Auftraege, die das Board braucht — laufende, offene Abnahmen, juengst Erledigtes."""
    jetzt = jetzt or datetime.now(timezone.utc)
    bedingung = and_(
        Task.team_id == team_id,
        or_(
            Task.status.in_(AKTIV),
            and_(Task.status.in_(ABNAHMEFAEHIG), Task.abgenommen_at.is_(None),
                 Task.created_at >= jetzt - FENSTER_ABNAHME),
            Task.completed_at >= jetzt - FENSTER_ERLEDIGT,
            Task.abgenommen_at >= jetzt - FENSTER_ERLEDIGT,
        ),
    )
    if sichtbar is not None:
        if not sichtbar:
            return []
        bedingung = and_(bedingung, Task.agent_id.in_(sichtbar))
    rows = (await db.execute(select(Task).where(bedingung).order_by(Task.created_at.desc()).limit(500))).scalars().all()
    return list(rows)


def _tagesverlauf(tasks: list[Task], jetzt: datetime, zone: ZoneInfo) -> list[dict]:
    heute = jetzt.astimezone(zone).date()
    tage = [heute - timedelta(days=i) for i in range(VERLAUF_TAGE - 1, -1, -1)]
    zaehler = {t: 0 for t in tage}
    for task in tasks:
        fertig = _utc(task.completed_at)
        if task.status != TaskStatus.COMPLETED or fertig is None:
            continue
        tag = fertig.astimezone(zone).date()
        if tag in zaehler:
            zaehler[tag] += 1
    return [{"tag": t.isoformat(), "anzahl": zaehler[t]} for t in tage]


def board_aus(tasks: list[Task], *, jetzt: datetime | None = None) -> dict:
    jetzt = jetzt or datetime.now(timezone.utc)
    zone = _zone()
    spalten: dict[str, list[dict]] = {"wartet": [], "in_arbeit": [], "zur_abnahme": [], "erledigt": []}
    for task in tasks:
        spalten[spalte(task)].append(karte(task))

    def _wann(k: dict) -> datetime:
        return k["abgenommen"] or k["fertig"] or k["erstellt"] or jetzt

    # Die Abfrage holt nicht abgenommene Ergebnisse 90 Tage zurueck — ob eines
    # eine Abnahme braucht, steht in den Metadaten. Was davon erledigt ist,
    # zeigt das Board nur so lange wie alles andere Erledigte.
    grenze = jetzt - FENSTER_ERLEDIGT
    spalten["erledigt"] = sorted(
        (k for k in spalten["erledigt"] if _wann(k) >= grenze), key=_wann, reverse=True
    )[:MAX_ERLEDIGT]
    spalten["wartet"].reverse()  # aelteste zuerst: so werden sie auch abgearbeitet
    verlauf = _tagesverlauf(tasks, jetzt, zone)
    heute_beginn = datetime.combine(jetzt.astimezone(zone).date(), time.min, tzinfo=zone)
    return {
        "spalten": spalten,
        "kennzahlen": {
            "wartet": len(spalten["wartet"]),
            "in_arbeit": len(spalten["in_arbeit"]),
            "zur_abnahme": len(spalten["zur_abnahme"]),
            "heute_fertig": sum(
                1 for t in tasks
                if t.status == TaskStatus.COMPLETED and t.completed_at and _utc(t.completed_at) >= heute_beginn
            ),
            "verlauf": verlauf,
            "fertig_7_tage": sum(v["anzahl"] for v in verlauf),
        },
    }


async def board(db: AsyncSession, team_id: str, sichtbar: list[str] | None) -> dict:
    return board_aus(await auftraege(db, team_id, sichtbar))


def titel_aus(text: str, n: int = 80) -> str:
    """Kurzer Titel aus der ersten Zeile eines Auftrags."""
    erste = next((z.strip() for z in text.splitlines() if z.strip()), "Auftrag")
    return erste if len(erste) <= n else erste[: n - 1].rstrip() + "…"


def auftragstext(task: Task) -> str:
    """Der Auftrag, wie der Mensch ihn erteilt hat — ohne Lead-Anleitung und Roster."""
    return ((task.metadata_ or {}).get("auftrag") or task.prompt or "").strip()


def nacharbeit_prompt(task: Task, hinweis: str) -> str:
    ergebnis = (task.result or task.error or "").strip()
    if len(ergebnis) > 4000:
        ergebnis = ergebnis[:4000] + "\n[… gekürzt]"
    return (
        f"Nacharbeit zu deinem Auftrag „{task.title}“. Der Auftraggeber hat das Ergebnis "
        f"zurückgegeben mit diesem Hinweis:\n\n{hinweis.strip()}\n\n"
        f"Ursprünglicher Auftrag:\n{auftragstext(task)[:4000]}\n\n"
        f"Dein bisheriges Ergebnis:\n{ergebnis or '(keins)'}"
    )
