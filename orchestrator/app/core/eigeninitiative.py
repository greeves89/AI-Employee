"""Eigeninitiative eines Agenten: die System-Zeitpläne, an EINER Stelle (#913).

Die Plattform legt je Agent bis zu drei Zeitpläne selbst an: ``[Proactive]`` (der
stündliche Eigeninitiative-Lauf) und zwei ``[Rhythmus]``-Läufe (Abendplanung,
Morgencheck, ``core/plan_rhythm``). Bisher bekam JEDER neue Agent sie ungefragt —
ohne Verantwortungsbereiche wurde aber jeder dieser Läufe übersprungen: keine
Wirkung, nur Wecken, Hinweise und Verwirrung.

Die Invariante: System-Zeitpläne existieren genau dann, wenn der Agent
Verantwortungsbereiche hat UND seine Eigeninitiative an ist
(``config['proactive']['enabled']``, fehlt der Eintrag: an). ``abgleichen`` stellt sie
her und ist die EINZIGE Stelle, die diese Zeitpläne anlegt oder entfernt. Aufgerufen
wird sie überall, wo sich eine der beiden Bedingungen ändert: Anlegen, Vorlage
anwenden, Einstellungen (``POST /agents/{id}/proactive``), Einrichtung im Chat
(``complete_onboarding``, Text und Sprache) — und beim Start als Aufräumen des
Altbestands (``ohne_bereiche_aufraeumen``).

Speichern (``commit``) muss jeweils der Aufrufer — er hat meist noch mehr zu sichern.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import plan_rhythm
from app.core.onboarding import has_duties
from app.models.schedule import Schedule

logger = logging.getLogger(__name__)

PROAKTIV_PRAEFIX = "[Proactive] "
STANDARD_TAKT_SEKUNDEN = 3600
# Erster Eigeninitiative-Lauf kurz nach dem Einschalten — nicht erst in einer Stunde,
# aber auch nicht mitten in das Gespräch, in dem die Bereiche gerade vergeben wurden.
ERSTER_LAUF_NACH = timedelta(minutes=10)


def eigeninitiative_an(agent) -> bool:
    """Ist die Eigeninitiative an? Fehlt die Angabe, gilt: an."""
    proactive = ((getattr(agent, "config", None) or {}).get("proactive") or {})
    return bool(proactive.get("enabled", True))


def soll_system_zeitplaene(agent) -> bool:
    """Die Invariante: Verantwortungsbereiche UND Eigeninitiative an."""
    return has_duties(agent) and eigeninitiative_an(agent)


def _system_zeitplan_filter():
    """SQL-Bedingung "System-Zeitplan" — dieselben Praefixe wie ``ist_system_zeitplan``."""
    from app.core.task_router import SYSTEM_ZEITPLAN_PRAEFIXE
    return or_(*(Schedule.name.startswith(p) for p in SYSTEM_ZEITPLAN_PRAEFIXE))


def _takt(sekunden: int) -> str:
    if sekunden == 3600:
        return "stündlich"
    if sekunden % 3600 == 0:
        return f"alle {sekunden // 3600} Stunden"
    return f"alle {max(1, sekunden // 60)} Minuten"


async def rhythmus_sicherstellen(
    db: AsyncSession, agent, vorhandene: dict[str, Schedule], now: datetime,
) -> int:
    """Die zwei ``[Rhythmus]``-Zeitpläne anlegen oder an die Dienstzeit anpassen.

    ``vorhandene``: die Rhythmus-Zeitpläne dieses Agenten nach Name. Ein vom Nutzer
    abgeschalteter Zeitplan (``enabled=False``) bleibt abgeschaltet — nur seine
    Uhrzeit wird nachgezogen. Gibt die Zahl NEU angelegter Zeitpläne zurück.
    """
    from app.services.scheduler_service import _calc_next_run

    angelegt = 0
    crons = plan_rhythm.cron_expressions(agent)
    for name, cron in (
        (plan_rhythm.EVENING_SCHEDULE_NAME, crons["evening"]),
        (plan_rhythm.MORNING_SCHEDULE_NAME, crons["morning"]),
    ):
        found = vorhandene.get(name)
        if found is not None:
            if found.cron_expression != cron or found.timezone != crons["timezone"]:
                found.cron_expression = cron
                found.timezone = crons["timezone"]
                found.next_run_at = _calc_next_run(found, now)
                logger.info("[Rhythmus] %s fuer %s auf %s (%s) gesetzt",
                            name, agent.id, cron, crons["timezone"])
            continue
        sched = Schedule(
            id=uuid.uuid4().hex[:8],
            name=name,
            # Der Text wird beim Feuern aus dem Code gebaut (siehe
            # SchedulerService._execute_schedule) — hier steht nur, was in der UI lesbar ist.
            prompt=(
                "Wird beim Ausführen aus dem Code gebaut: "
                "Tagesplanung am Abend bzw. Durchsicht am Morgen."
            ),
            interval_seconds=0,
            cron_expression=cron,
            timezone=crons["timezone"],
            priority=0,
            agent_id=agent.id,
            enabled=True,
            next_run_at=now,
        )
        sched.next_run_at = _calc_next_run(sched, now)
        db.add(sched)
        angelegt += 1
        logger.info("[Rhythmus] %s fuer %s angelegt (%s, %s)",
                    name, agent.id, cron, crons["timezone"])
    return angelegt


def _proactive_mit(agent, **aenderungen) -> None:
    """``config['proactive']`` ändern — als NEUES dict, damit das JSON-Feld als
    geändert gilt. ``None`` entfernt einen Schlüssel."""
    config = dict(agent.config or {})
    proactive = dict(config.get("proactive") or {})
    for key, wert in aenderungen.items():
        if wert is None:
            proactive.pop(key, None)
        else:
            proactive[key] = wert
    config["proactive"] = proactive
    agent.config = config


def _hinweis(db: AsyncSession, agent, takt_sekunden: int) -> None:
    """EINE Benachrichtigung, wenn die Eigeninitiative durch neue Bereiche anläuft."""
    from app.models.notification import Notification

    zeiten = plan_rhythm.rhythm_times(agent)
    db.add(Notification(
        agent_id=agent.id,
        type="info",
        title=f"{agent.name} arbeitet jetzt selbstständig",
        message=(
            f"{agent.name} hat jetzt Verantwortungsbereiche und arbeitet sie von sich aus ab: "
            f"{_takt(takt_sekunden)}, Tagesplanung um {zeiten['evening']} Uhr, "
            f"Durchsicht um {zeiten['morning']} Uhr."
        )[:240],
        priority="normal",
        action_url=f"/agents/{agent.id}",
        meta={"reason": "eigeninitiative_aktiv"},
    ))


async def abgleichen(
    db: AsyncSession, agent, *, hinweis: bool = True, now: datetime | None = None,
) -> dict:
    """System-Zeitpläne dieses Agenten an die Invariante angleichen.

    * Bereiche + Eigeninitiative an: ``[Proactive]`` (eingeschaltet, Takt aus
      ``config['proactive']['interval_seconds']``) und beide ``[Rhythmus]`` sicherstellen.
      Entsteht der ``[Proactive]``-Zeitplan dabei neu, bekommt der Besitzer EINE
      Benachrichtigung (``hinweis=False``, wo die Oberfläche es schon anzeigt).
    * sonst: alle System-Zeitpläne des Agenten entfernen. Eigene Zeitpläne des Nutzers
      bleiben unangetastet.

    Gibt ``{"angelegt": n, "entfernt": m}`` zurück. Speichern muss der Aufrufer.
    """
    now = now or datetime.now(timezone.utc)
    eigene = list((await db.execute(
        select(Schedule).where(Schedule.agent_id == agent.id, _system_zeitplan_filter())
    )).scalars().all())
    proaktiv = [s for s in eigene if s.name.startswith(PROAKTIV_PRAEFIX)]
    rhythmus = {s.name: s for s in eigene if s.name.startswith(plan_rhythm.SCHEDULE_PREFIX)}
    gemerkt = ((agent.config or {}).get("proactive") or {}).get("schedule_id")

    if not soll_system_zeitplaene(agent):
        for s in eigene:
            await db.delete(s)
        if gemerkt:
            _proactive_mit(agent, schedule_id=None)
        if eigene:
            logger.info("[Eigeninitiative] %d System-Zeitplan/-plaene von %s entfernt "
                        "(keine Bereiche oder Eigeninitiative aus)", len(eigene), agent.id)
        return {"angelegt": 0, "entfernt": len(eigene)}

    takt = int(((agent.config or {}).get("proactive") or {}).get("interval_seconds")
               or STANDARD_TAKT_SEKUNDEN)
    angelegt = 0
    entfernt = 0
    behalten = next((s for s in proaktiv if s.id == gemerkt), proaktiv[0] if proaktiv else None)
    for doppelt in proaktiv:
        if doppelt is not behalten:
            await db.delete(doppelt)
            entfernt += 1
    if behalten is None:
        from app.core.agent_manager import PROACTIVE_PROMPT
        behalten = Schedule(
            id=uuid.uuid4().hex[:8],
            name=f"{PROAKTIV_PRAEFIX}{agent.name}",
            # Der Basistext kommt beim Feuern immer aus dem Code (Scheduler);
            # diese Kopie ist nur ein Platzhalter fuer die Zeile.
            prompt=PROACTIVE_PROMPT,
            interval_seconds=takt,
            priority=0,
            agent_id=agent.id,
            enabled=True,
            next_run_at=now + ERSTER_LAUF_NACH,
        )
        db.add(behalten)
        angelegt += 1
        if hinweis:
            _hinweis(db, agent, takt)
        logger.info("[Eigeninitiative] [Proactive] fuer %s angelegt (%ss)", agent.id, takt)
    elif not behalten.enabled or behalten.interval_seconds != takt:
        behalten.enabled = True
        behalten.interval_seconds = takt
        behalten.next_run_at = now + timedelta(seconds=takt)
    if gemerkt != behalten.id:
        _proactive_mit(agent, schedule_id=behalten.id)

    angelegt += await rhythmus_sicherstellen(db, agent, rhythmus, now)
    return {"angelegt": angelegt, "entfernt": entfernt}


async def ohne_bereiche_aufraeumen(db: AsyncSession) -> int:
    """Startup-Aufräumen: System-Zeitpläne von Agenten OHNE Bereiche entfernen.

    Verhaltensneutral — deren Läufe wurden ohnehin übersprungen. Agenten MIT Bereichen
    bleiben unberührt (auch wenn ihre Eigeninitiative aus ist): hier wird nur der
    Altbestand aus der Zeit bereinigt, als jeder Agent die Zeitpläne ungefragt bekam.
    Speichert selbst. Gibt die Zahl entfernter Zeitpläne zurück.
    """
    from app.models.agent import Agent

    agent_ids = set((await db.execute(
        select(Schedule.agent_id).where(Schedule.agent_id.isnot(None), _system_zeitplan_filter())
    )).scalars().all())
    if not agent_ids:
        return 0
    agents = (await db.execute(select(Agent).where(Agent.id.in_(agent_ids)))).scalars().all()
    entfernt = 0
    for agent in agents:
        if has_duties(agent):
            continue
        entfernt += (await abgleichen(db, agent, hinweis=False))["entfernt"]
    await db.commit()
    return entfernt
