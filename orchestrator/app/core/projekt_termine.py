"""Termine eines Projekts: Daily, Weekly oder einmalig — als Besprechung der Mitglieder.

Ein Termin ist ein Zeitplan (``schedules.team_id``) mit ``__meeting__``-Auftrag;
der Scheduler legt zur Zeit einen Besprechungsraum mit ``team_id`` an und startet
ihn (``scheduler_service._execute_meeting_schedule``). Ablauf und Stufen kommen aus
den Vorlagen (``core/meeting_templates``).

Am Ende einer Projekt-Besprechung (``abschluss``):
* das Protokoll (Zusammenfassung mit Action Items) wird eine Seite im
  Projektwissen (``protokolle/…``) und eine Nachricht im Projektgespräch;
* jedes Action Item wird eine Karte unter „Bereit“ — der Lead (oder der Takt)
  vergibt sie. Es entstehen keine Aufträge an Agenten vorbei am Board.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schedule import Schedule
from app.models.team import Team

log = logging.getLogger(__name__)

PRAEFIX = "[Projekt-Termin] "
ARTEN = ("daily", "weekly", "einmalig")
WOCHENTAGE = {"mo": 1, "di": 2, "mi": 3, "do": 4, "fr": 5, "sa": 6, "so": 0}
VORLAGE_JE_ART = {"daily": "daily", "weekly": "entscheidung", "einmalig": "entscheidung"}
MAX_ITEMS = 15


class TerminFehler(ValueError):
    pass


def _zone() -> str:
    from app.core.agent_duty import anlagen_zeitzone

    return anlagen_zeitzone()


def _uhrzeit(wert: str) -> tuple[int, int]:
    m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", (wert or "").strip())
    if not m:
        raise TerminFehler("Uhrzeit bitte als HH:MM angeben, z. B. 09:00.")
    return int(m.group(1)), int(m.group(2))


def teilnehmer(team: Team) -> list[str]:
    ids = list(dict.fromkeys([*(team.member_agent_ids or []), *([team.lead_agent_id] if team.lead_agent_id else [])]))
    return ids


def termin_dict(s: Schedule) -> dict:
    try:
        cfg = json.loads(s.prompt[len("__meeting__:"):])
    except Exception:  # noqa: BLE001
        cfg = {}
    return {
        "id": s.id, "art": cfg.get("projekt_art", "einmalig"), "thema": cfg.get("topic", ""),
        "uhrzeit": cfg.get("projekt_uhrzeit"), "wochentag": cfg.get("projekt_wochentag"),
        "cron": s.cron_expression, "aktiv": s.enabled,
        "naechster": s.next_run_at.isoformat() if s.enabled and s.next_run_at else None,
        "zuletzt": s.last_run_at.isoformat() if s.last_run_at else None,
    }


TAG_NAME = {"mo": "Montag", "di": "Dienstag", "mi": "Mittwoch", "do": "Donnerstag", "fr": "Freitag",
            "sa": "Samstag", "so": "Sonntag"}


def beschreibung(termin: dict, datum: str | None = None) -> str:
    """Lesbar für Gespräch und Bericht: „Daily (Mo–Fr) um 09:00“, „wöchentlich sonntags um 07:15“."""
    uhr = termin.get("uhrzeit") or ""
    if termin.get("art") == "daily":
        return f"Daily (Mo–Fr) um {uhr}"
    if termin.get("art") == "weekly":
        tag = TAG_NAME.get(termin.get("wochentag") or "", "")
        return f"wöchentlich {tag.lower() + 's ' if tag else ''}um {uhr}"
    try:
        tag = datetime.fromisoformat((datum or "").strip()).strftime("%d.%m.%Y")
    except ValueError:
        tag = ""
    return f"einmalig{' am ' + tag if tag else ''} um {uhr}"


async def liste(db: AsyncSession, team_id: str) -> list[Schedule]:
    return list((await db.execute(
        select(Schedule).where(Schedule.team_id == team_id).order_by(Schedule.next_run_at)
    )).scalars().all())


async def anlegen(db: AsyncSession, team: Team, *, art: str, uhrzeit: str, wochentag: str | None = None,
                  datum: str | None = None, thema: str | None = None, von_user_id: str | None = None) -> Schedule:
    """Termin anlegen. Der Aufrufer committet."""
    from app.core.meeting_templates import TEMPLATES
    from app.services.scheduler_service import _calc_next_run

    if art not in ARTEN:
        raise TerminFehler(f"Art: {', '.join(ARTEN)}.")
    agenten = teilnehmer(team)
    if len(agenten) < 2:
        raise TerminFehler("Eine Besprechung braucht mindestens zwei Agenten im Projekt.")
    stunde, minute = _uhrzeit(uhrzeit)
    zone = _zone()
    cron = None
    jetzt = datetime.now(timezone.utc)
    if art == "daily":
        cron = f"{minute} {stunde} * * 1-5"
    elif art == "weekly":
        tag = WOCHENTAGE.get((wochentag or "").strip().lower()[:2])
        if tag is None:
            raise TerminFehler("Wochentag: Mo, Di, Mi, Do, Fr, Sa oder So.")
        cron = f"{minute} {stunde} * * {tag}"
    else:
        try:
            tag = datetime.fromisoformat((datum or "").strip())
        except ValueError:
            raise TerminFehler("Datum bitte als JJJJ-MM-TT angeben.")
        start = datetime(tag.year, tag.month, tag.day, stunde, minute, tzinfo=ZoneInfo(zone)).astimezone(timezone.utc)
        if start <= jetzt:
            raise TerminFehler("Der Termin liegt in der Vergangenheit.")

    vorlage = TEMPLATES[VORLAGE_JE_ART[art]]
    standard = {"daily": "Daily", "weekly": "Weekly", "einmalig": "Besprechung"}[art]
    thema = (thema or "").strip()[:300] or f"{standard} im Projekt {team.name}: Stand, Hindernisse, nächste Schritte"
    config = {
        "name": f"{standard}: {team.name}"[:200], "topic": thema, "agent_ids": agenten,
        "max_rounds": vorlage["max_rounds"], "stages_config": vorlage["stages"],
        "use_moderator": False, "initial_message": f"{standard} des Projekts „{team.name}“ beginnt.",
        "created_by": f"user:{von_user_id}" if von_user_id else "projekt", "team_id": team.id,
        "projekt_art": art, "projekt_uhrzeit": f"{stunde:02d}:{minute:02d}",
        "projekt_wochentag": (wochentag or "").strip().lower()[:2] or None,
    }
    sched = Schedule(
        id=uuid.uuid4().hex[:12], name=f"{PRAEFIX}{standard}: {team.name}"[:200],
        prompt="__meeting__:" + json.dumps(config, ensure_ascii=False), interval_seconds=0,
        cron_expression=cron, timezone=zone, agent_id=None, enabled=True, next_run_at=jetzt,
        team_id=team.id, created_by=von_user_id,
    )
    sched.next_run_at = _calc_next_run(sched, jetzt) if cron else start
    db.add(sched)
    return sched


async def entfernen(db: AsyncSession, team_id: str, schedule_id: str) -> None:
    sched = await db.get(Schedule, schedule_id)
    if sched is None or sched.team_id != team_id:
        raise LookupError("Termin nicht gefunden.")
    await db.delete(sched)


def _items(markdown: str) -> list[str]:
    return [z.strip()[5:].strip() for z in (markdown or "").splitlines() if z.strip().startswith("- [ ]")][:MAX_ITEMS]


async def abschluss(room_id: str, team_id: str, raum_name: str, zusammenfassung: str, agent_ids: list[str]) -> None:
    """Ende einer Projekt-Besprechung: Protokoll ins Wissen und ins Gespräch, Items als Karten."""
    from app.core import projekt_wissen, projekte
    from app.db.session import async_session_factory
    from app.models.agent import Agent
    from app.models.projekt_nachricht import ProjektNachricht

    async with async_session_factory() as db:
        team = await db.get(Team, team_id)
        if team is None or not team.is_active:
            return
        namen = {a.id: a.name for a in (await db.execute(select(Agent).where(Agent.id.in_(agent_ids or [])))).scalars()}
        zone = ZoneInfo(_zone())
        jetzt = datetime.now(zone)
        slug = re.sub(r"[^a-z0-9]+", "-", raum_name.lower()).strip("-")[:40] or "besprechung"
        pfad = f"protokolle/{jetzt:%Y-%m-%d-%H%M}-{slug}.md"
        protokoll = (
            f"# {raum_name} — {jetzt:%d.%m.%Y %H:%M}\n\n"
            f"Teilnehmer: {', '.join(namen.get(a, a) for a in agent_ids) or '—'}\n\n"
            f"{(zusammenfassung or '').strip()}\n"
        )
        try:
            brain = await projekt_wissen.sicherstellen(db, team)
            await projekt_wissen.schreiben(db, brain, pfad, protokoll, wer=f"Besprechung {raum_name}")
        except Exception as e:  # noqa: BLE001 — das Protokoll im Gespräch ist der Rückhalt
            log.warning("Protokoll %s nicht ins Wissen geschrieben: %s", room_id, e)
            pfad = None

        items = _items(zusammenfassung)
        for item in items:
            # Ohne Ersteller: die Items schreiben Agenten (womöglich nach Issue-Texten) —
            # der Text geht deshalb gerahmt als Material an den Bearbeiter, nicht als
            # Anweisung im Namen des Projekt-Erstellers.
            karte = projekte.neue_karte(team.id, item[:300], f"Aus der Besprechung „{raum_name}“ ({jetzt:%d.%m.%Y}).",
                                        spalte="bereit", von=f"besprechung:{room_id}", ersteller_user_id=None)
            karte.notiz = "Aus Besprechung"
            db.add(karte)
        kurz = (zusammenfassung or "").strip()
        if len(kurz) > 1500:
            kurz = kurz[:1500].rstrip() + " …"
        db.add(ProjektNachricht(
            team_id=team.id, autor_typ="system", autor_id=None, autor_name="Besprechung",
            text=(f"Protokoll „{raum_name}“" + (f" (Projektwissen: {pfad})" if pfad else "") +
                  (f" — {len(items)} Karte(n) unter „Bereit“ angelegt" if items else "") + f"\n\n{kurz}"),
            art="protokoll", an=[],
        ))
        await db.commit()
