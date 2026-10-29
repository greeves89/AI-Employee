"""Termine eines Projekts: Daily, Weekly oder einmalig — als Besprechung der Mitglieder.

Ein Termin ist ein Zeitplan (``schedules.team_id``) mit ``__meeting__``-Auftrag;
der Scheduler legt zur Zeit einen Besprechungsraum mit ``team_id`` an und startet
ihn (``scheduler_service._execute_meeting_schedule``). Ablauf und Stufen kommen aus
den Vorlagen (``core/meeting_templates``).

Am Ende einer Projekt-Besprechung (``abschluss``):
* das Protokoll (Zusammenfassung mit Action Items) wird eine Seite im
  Projektwissen (``protokolle/…``) und eine Nachricht im Projektgespräch;
* jedes Action Item wird eine Karte in der „Inbox“ — ein Mensch gibt sie erst frei,
  danach vergibt der Lead (oder der Takt) sie aus „Bereit“. Es entstehen keine
  Aufträge an Agenten vorbei am Board.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
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


#: Was eine Projekt-Besprechung vom Board sieht: alles, worüber man spricht —
#: nicht die Inbox (Kennzahl genügt), dafür offene Rückfragen (Triage) und das
#: kürzlich Erledigte/Verworfene, damit niemand ein längst geschlossenes Issue
#: erneut aufgreift.
STAND_SPALTEN = ("bereit", "in_arbeit", "triage", "pruefung", "erledigt", "verworfen")


async def stand_jetzt(db: AsyncSession, team_id: str) -> str:
    """Stand des Projekts für die Besprechung — dieselbe Darstellung wie ``projekt_board``.

    Ohne ihn sprachen die Agenten aus ihrem Gedächtnis: Ein Agent brachte jeden
    Morgen ein seit Tagen geschlossenes Issue ein, und aus jedem Daily wurden
    7–9 Karten dazu (19.–28.10.2026).
    """
    from app.core import projekte
    from app.models.agent import Agent

    team = await db.get(Team, team_id)
    if team is None:
        return ""
    b = await projekte.board(db, team_id, None)
    ids = {k.get("agent_id") for s in b["spalten"].values() for k in s} | {lauf["agent_id"] for lauf in b["laufend"]}
    ids.discard(None)
    namen = {a.id: a.name for a in (await db.execute(select(Agent).where(Agent.id.in_(ids)))).scalars()} if ids else {}
    return projekte.board_als_text({"name": team.name, "id": team.id}, b, namen, je_spalte=10, spalten=STAND_SPALTEN)


_ISSUE_NR = re.compile(r"#(\d{1,6})\b")


def _vergleichstext(text: str) -> str:
    return re.sub(r"\W+", " ", (text or "").lower()).strip()[:300]


_FUELL = frozenset("der die das und oder mit für fuer von vor nach auf aus ein eine einen einem einer dem den des "
                   "ist sind wird werden bis bei zum zur als auch noch nicht nur dann dass wenn jede jeden jedes "
                   "heute morgen danach zuvor mindestens minuten meetingende".split())
#: Ab so viel Wortüberlappung gilt ein Punkt als schon besprochen. Die Agenten
#: formulieren denselben Punkt jeden Tag anders — reine Textgleichheit fing nichts.
AEHNLICH_AB = 0.6
#: Erledigte Karten, gegen die noch verglichen wird: was vor kurzem erledigt wurde,
#: soll die nächste Besprechung nicht wieder anlegen.
ERLEDIGT_FENSTER = timedelta(days=14)
#: Mehr Karten je Besprechung sind Rauschen, kein Plan.
MAX_KARTEN = 5
#: Höchstens so viele Nummern je Besprechung beim Hoster nachfragen — ein Protokoll
#: voller Nummern darf das Ende der Besprechung nicht aufhalten.
MAX_STATUSABFRAGEN = 20
#: Frist für alle Statusabfragen zusammen (die Zeitgrenze von httpx gilt je Lesevorgang).
STATUS_FRIST_SEKUNDEN = 15


def _woerter(text: str) -> frozenset[str]:
    return frozenset(w for w in _vergleichstext(text).split() if len(w) >= 4 and w not in _FUELL)


def _aehnlich(a: frozenset[str], b: frozenset[str]) -> bool:
    """Überlappung bezogen auf den kürzeren Text: ein knapper Punkt, der ganz in
    einer längeren Karte steckt, ist derselbe Punkt."""
    if not a or not b:
        return False
    if min(len(a), len(b)) < 3:
        # Zwei Wörter treffen zu leicht irgendeine lange Karte — dann nur Gleiches.
        return a == b
    return len(a & b) / min(len(a), len(b)) >= AEHNLICH_AB


def _items(markdown: str) -> list[str]:
    return [z.strip()[5:].strip() for z in (markdown or "").splitlines() if z.strip().startswith("- [ ]")][:MAX_ITEMS]


async def _geschlossen_an_der_quelle(redis, team_id: str, repo: str, nummern: set[int]) -> set[int]:
    """Welche der genannten Issue-Nummern sind beim Hoster wirklich geschlossen?

    Die Board-Spalte taugt dafür nicht: ``spalte == "erledigt"`` kennt nur Nummern,
    zu denen überhaupt noch eine Karte liegt — gemessen am 28.10.2026 waren das
    4 von 133 Issue-Karten, während im Repo hunderte Issues geschlossen sind. Ein
    Protokollpunkt zu einem geschlossenen Issue *ohne* Karte passierte die Wache
    deshalb jedes Mal und wurde wieder zur Karte; das Aufräumen von Hand half nicht,
    weil mit der Karte auch das einzige Kennzeichen verschwand.

    Eine nicht erreichbare Quelle heißt ausdrücklich *nicht* geschlossen — und das
    gilt für beide Arten von „nicht erreichbar": eine Ausnahme UND die Antwort
    ``None``, mit der die Anbieter 404/401/403 und eine erschöpfte Abfragegrenze
    melden. Ein doppelter Punkt ist billiger als ein verlorener. Schon gemessene
    Nummern bleiben gültig — bricht die Abfrage nach der Hälfte ab, zählt die Hälfte.

    Läuft in einer EIGENEN Sitzung und bekommt deshalb nur Werte, kein ORM-Objekt.
    """
    if not nummern or redis is None or not repo:
        return set()

    import httpx

    from app.core import projekt_git
    from app.db.session import async_session_factory

    zu: set[int] = set()
    try:
        ziel = projekt_git.repo_ziel(repo)
        if ziel.art == "ungueltig" or not ziel.pfad:
            return zu
        async with asyncio.timeout(STATUS_FRIST_SEKUNDEN):
            # EIGENE Sitzung: ``zugang`` kann beim Token-Tausch auf der übergebenen
            # Sitzung ein ``rollback`` auslösen (``oauth_service`` tut das, wenn sein
            # ``commit`` scheitert). Das ließe alle geladenen Objekte des Aufrufers
            # verfallen — die Karten dieser Besprechung wären verloren, der nächste
            # Zugriff ein Nachladen außerhalb des Async-Kontexts (MissingGreenlet).
            # ``zugang`` liegt mit in der Frist: es tauscht selbst Token über HTTP
            # und wartet auf eine Datenbanksperre.
            async with async_session_factory() as eigene:
                team = await eigene.get(Team, team_id)
                if team is None:
                    return zu
                token, anbieter = await projekt_git.zugang(eigene, redis, team)
                async with httpx.AsyncClient(timeout=httpx.Timeout(10.0), follow_redirects=False) as client:
                    for nr in sorted(nummern)[:MAX_STATUSABFRAGEN]:
                        if await anbieter.issue_state(client, token, ziel.pfad, nr) != "closed":
                            continue
                        # ``/issues/{n}`` antwortet bei GitHub AUCH für Pull Requests, und
                        # ``#N`` steht in einem Protokoll oft für etwas anderes als ein Issue
                        # („PR #1126 nachziehen", „Punkt #3 priorisieren"). In einem
                        # gewachsenen Repo ist fast jede Zahl „geschlossen" — ein PR ist aber
                        # kein erledigtes Issue, und so ein Punkt darf nicht verschwinden.
                        if await anbieter.pull_state(client, token, ziel.pfad, nr) is not None:
                            continue
                        zu.add(nr)
    except Exception as e:  # noqa: BLE001 — die Besprechung endet auch ohne Hoster
        log.warning("Issue-Status für Projekt %s nicht vollständig geprüft: %s", team_id, e)
    return zu


async def abschluss(room_id: str, team_id: str, raum_name: str, zusammenfassung: str, agent_ids: list[str],
                    redis=None) -> None:
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

        # Nicht jeden Punkt blind zur Karte machen: was nur geschlossene Issues
        # betrifft oder schon als offene Karte dasteht, wird übersprungen.
        from app.models.projekt_karte import ProjektKarte

        karten = (await db.execute(
            select(ProjektKarte.quelle, ProjektKarte.extern_nr, ProjektKarte.spalte, ProjektKarte.titel,
                   ProjektKarte.erledigt_at)
            .where(ProjektKarte.team_id == team.id)
        )).all()
        geschlossen = {nr for quelle, nr, spalte, _, _ in karten
                       if quelle == "issue" and spalte in ("erledigt", "verworfen") and nr}
        # Verglichen wird mit allem Offenen, mit kürzlich Erledigtem und mit jedem
        # geschlossenen Issue — per Wortüberlappung. Gemessen 26.–28.10.: Das Daily
        # legte jeden Morgen bis zu zehn Karten zum längst geschlossenen #961 an,
        # immer anders formuliert und ohne „#961“ im Text; der Lead räumte sie
        # jeden Takt wieder ab.
        grenze = datetime.now(timezone.utc) - ERLEDIGT_FENSTER
        vergleich = [
            _woerter(titel) for quelle, _, spalte, titel, erledigt_at in karten
            if spalte not in ("erledigt", "verworfen") or quelle == "issue"
            or (erledigt_at is not None and (erledigt_at if erledigt_at.tzinfo else
                                             erledigt_at.replace(tzinfo=timezone.utc)) >= grenze)
        ]
        punkte = _items(zusammenfassung)
        # Den Status der genannten Issues an der QUELLE messen, nicht am Kartenzustand:
        # ohne Karte kennt das Board eine geschlossene Nummer gar nicht (#1106).
        genannt = {int(n) for p in punkte for n in _ISSUE_NR.findall(p)}
        geschlossen |= await _geschlossen_an_der_quelle(redis, team.id, team.repo or "", genannt - geschlossen)
        items, uebersprungen = [], 0
        for item in punkte:
            nummern = {int(n) for n in _ISSUE_NR.findall(item)}
            woerter = _woerter(item)
            if (nummern and nummern <= geschlossen) or any(_aehnlich(woerter, v) for v in vergleich):
                uebersprungen += 1
                continue
            if len(items) >= MAX_KARTEN:
                uebersprungen += 1
                continue
            vergleich.append(woerter)
            items.append(item)
        for item in items:
            # Ohne Ersteller: die Items schreiben Agenten (womöglich nach Issue-Texten) —
            # der Text geht deshalb gerahmt als Material an den Bearbeiter, nicht als
            # Anweisung im Namen des Projekt-Erstellers.
            # In die Inbox, nicht nach Bereit: Was eine Besprechung vorschlägt, gibt
            # ein Mensch erst frei — gearbeitet wird nur an Bereitem.
            karte = projekte.neue_karte(team.id, item[:300], f"Aus der Besprechung „{raum_name}“ ({jetzt:%d.%m.%Y}).",
                                        spalte="eingang", von=f"besprechung:{room_id}", ersteller_user_id=None)
            karte.notiz = "Aus Besprechung"
            db.add(karte)
        kurz = (zusammenfassung or "").strip()
        if len(kurz) > 1500:
            kurz = kurz[:1500].rstrip() + " …"
        db.add(ProjektNachricht(
            team_id=team.id, autor_typ="system", autor_id=None, autor_name="Besprechung",
            text=(f"Protokoll „{raum_name}“" + (f" (Projektwissen: {pfad})" if pfad else "") +
                  (f" — {len(items)} Karte(n) in der Inbox angelegt" if items else "") +
                  (f", {uebersprungen} Punkt(e) übersprungen (schon als Karte da, kürzlich erledigt, Issue "
                   f"geschlossen oder mehr als {MAX_KARTEN})"
                   if uebersprungen else "") + f"\n\n{kurz}"),
            art="protokoll", an=[],
        ))
        await db.commit()
