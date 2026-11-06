"""Agenten aktuell halten — ohne dass ein Admin daran denken muss.

Ein Release bringt oft etwas, das erst im Agenten wirkt: ein neues Abbild, eine
Sicherheitsregel im Hook, eine andere Proxy-Stufe. Bisher kam das nur an, wenn jemand
„Alle aktualisieren“ klickte. Beim Kunden heißt das: oft nie. Und ein gestoppter Agent
startete auf seinem alten Behälter weiter (``AgentManager.start_agent``).

Hier gilt deshalb:

* **Im Leerlauf** (Einstellung ``agenten_auto_update`` = ``leerlauf``, Vorgabe): Ein
  Agent, der aktualisiert werden muss und gerade nichts tut, wird neu erstellt. Pro
  Durchlauf höchstens EINER — gestaffelt, denn Codex-Agenten teilen sich eine
  Anmeldung und ein gleichzeitiges Neuerstellen hat sie früher alle lahmgelegt.
* **Beim Start**: Ein veralteter, gestoppter Agent wird beim Start neu erstellt statt
  auf dem alten Behälter hochgefahren (``start_agent``). Gilt immer, auch bei ``aus``:
  ein gestoppter Agent ist nie beschäftigt.
* **Nie mitten in der Arbeit**: ``beschaeftigt`` ist die eine Stelle, die sagt, ob ein
  Agent gerade arbeitet. Der Leerlauf-Stopp im Scheduler fragt dieselbe.

Scheitert ein Aktualisieren, wird es für diese Version nicht wiederholt (Merkmal im
Agenten) — sonst risse der Takt alle fünf Minuten denselben Agenten ab. Jeder Lauf
steht im Prüfprotokoll.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import func, select
from sqlalchemy.orm.attributes import flag_modified

from app.config import get_agent_version, settings

logger = logging.getLogger(__name__)

EINSTELLUNG = "agenten_auto_update"
MODI = ("aus", "leerlauf")
VORGABE = "leerlauf"

#: So lange muss der letzte Chat-Austausch her sein. Kürzer würde mitten in ein
#: Gespräch fallen, in dem der Mensch gerade liest oder tippt.
RUHE = timedelta(minutes=15)

#: Merkmal im Agenten: für diese Version schon gescheitert, nicht erneut versuchen.
FEHLGESCHLAGEN = "auto_update_fehlgeschlagen"

#: Einstellung: nach dem ERSTEN Fehlschlag einer Version hält die Automatik ganz an.
#: Seit 1.434.1 springt ``update_agent`` bei einem Fehlschlag auf den alten Behälter
#: zurück; das Anhalten bleibt trotzdem: ein Fehler, der bei einem Agenten auftritt,
#: tritt meist bei allen auf — dann soll ein Mensch nachsehen, statt dass jeder Agent
#: alle fünf Minuten kurz angehalten wird.
ANGEHALTEN = "agenten_auto_update_angehalten"

#: Ergebnisse von ``DockerService.abbild_sicherstellen``, mit denen ein neuer Behälter entsteht.
ABBILD_BEREIT = ("lokal", "registry")

GRUENDE = {
    "arbeitet": "arbeitet gerade",
    "auftrag": "ein Auftrag läuft",
    "termin": "ist in einer Besprechung",
    "sprache": "führt gerade ein Sprachgespräch",
    "gespraech": "Gespräch in den letzten 15 Minuten",
    "fehlgeschlagen": "letzter Versuch gescheitert — bitte einzeln aktualisieren",
    "freigabe": "wartet auf eine Freigabe",
    "angehalten": "Automatik nach einem Fehlschlag angehalten — Abbild prüfen",
    "gestoppt": "gestoppt — wird beim nächsten Start aktualisiert",
    "aus": "automatisches Aktualisieren ist aus",
    "bereit": "wird beim nächsten Durchlauf aktualisiert",
}


def modus() -> str:
    wert = (getattr(settings, EINSTELLUNG, "") or "").strip().lower()
    return wert if wert in MODI else VORGABE


def angehalten() -> bool:
    """Ist die Automatik für DIESE Version nach einem Fehlschlag angehalten?"""
    return (getattr(settings, ANGEHALTEN, "") or "") == get_agent_version()


async def abbild_bereit(docker) -> bool:
    """Liegt das Agenten-Abbild vor (oder lässt es sich holen)? Sonst nichts neu erstellen."""
    import asyncio

    try:
        stand = await asyncio.to_thread(docker.abbild_sicherstellen, settings.agent_image)
    except Exception:  # noqa: BLE001 — wirft laut Vertrag nie; sicher ist sicher
        return False
    return stand in ABBILD_BEREIT


def veraltet(agent) -> bool:
    """Braucht der Agent einen neuen Behälter? (Version oder Proxy-Stufe)"""
    from app.core.egress import veraltet as egress_veraltet

    cfg = agent.config or {}
    return cfg.get("agent_version") != get_agent_version() or egress_veraltet(cfg, agent.id)


async def beschaeftigt(db, redis, agent, *, ruhe: timedelta | None = None) -> str | None:
    """Warum der Agent gerade NICHT angefasst werden darf — oder None.

    ``ruhe``: zusätzlich die Zeit seit dem letzten Chat-Austausch prüfen (fürs
    Aktualisieren). Der Leerlauf-Stopp misst Ruhe anders (``updated_at``) und lässt
    es weg.
    """
    from app.models.agent import AgentState
    from app.models.task import Task, TaskStatus

    if agent.state == AgentState.WORKING:
        return "arbeitet"
    try:
        live = await redis.get_agent_status(agent.id) if redis else {}
    except Exception:  # noqa: BLE001 — ohne Live-Stand zählt der Rest
        live = {}
    if live.get("state") == "working" or live.get("current_task"):
        return "arbeitet"
    if (await db.execute(select(Task.id).where(
            Task.agent_id == agent.id, Task.status == TaskStatus.RUNNING).limit(1))).scalar_one_or_none():
        return "auftrag"
    from app.core import sprachsitzungen

    if sprachsitzungen.aktiv(agent.id):
        return "sprache"
    if await _in_besprechung(db, agent.id):
        return "termin"
    from app.models.command_approval import ApprovalStatus, CommandApproval

    if (await db.execute(select(CommandApproval.id).where(
            CommandApproval.agent_id == agent.id,
            CommandApproval.status == ApprovalStatus.PENDING).limit(1))).scalar_one_or_none():
        # Ein Agent, der auf eine Entscheidung wartet, ruht oft länger als 15 Minuten —
        # ein neuer Behälter verlöre den Faden, an dem die Antwort hängt.
        return "freigabe"
    if ruhe is not None:
        from app.models.chat_message import ChatMessage

        zuletzt = (await db.execute(select(func.max(ChatMessage.timestamp)).where(
            ChatMessage.agent_id == agent.id))).scalar_one_or_none()
        if zuletzt is not None:
            if zuletzt.tzinfo is None:
                zuletzt = zuletzt.replace(tzinfo=UTC)
            if datetime.now(UTC) - zuletzt < ruhe:
                return "gespraech"
    return None


async def _in_besprechung(db, agent_id: str) -> bool:
    """Sitzt der Agent in einer laufenden Besprechung? (dort ruht er nur zwischen Beiträgen)"""
    from app.models.meeting_room import MeetingRoom

    return (await db.execute(select(MeetingRoom.id).where(
        MeetingRoom.state == "running", MeetingRoom.agent_ids.contains([agent_id])).limit(1))
    ).scalar_one_or_none() is not None


def _laeuft(agent) -> bool:
    from app.models.agent import AgentState

    return agent.state in (AgentState.RUNNING, AgentState.IDLE, AgentState.WORKING)


async def grund(db, redis, agent) -> str:
    """Für die Anzeige: warum ein veralteter Agent (noch) nicht aktualisiert ist."""
    if not _laeuft(agent):
        return "gestoppt"
    if (agent.config or {}).get(FEHLGESCHLAGEN) == get_agent_version():
        return "fehlgeschlagen"
    if modus() == "aus":
        return "aus"
    if angehalten():
        return "angehalten"
    return await beschaeftigt(db, redis, agent, ruhe=RUHE) or "bereit"


async def _protokollieren(db, agent, erfolg: bool, fehler: str = "") -> None:
    from app.core.audit import protokolliere
    from app.models.audit_log import AuditEventType

    await protokolliere(
        db, AuditEventType.AGENT_UPDATED, agent_id=agent.id,
        command=(f"Automatisch aktualisiert auf {get_agent_version()}" if erfolg
                 else f"Automatisches Aktualisieren auf {get_agent_version()} gescheitert"),
        outcome="success" if erfolg else "failure",
        meta={"ausloeser": "leerlauf", "version": get_agent_version(), **({"fehler": fehler[:300]} if fehler else {})},
    )


async def _anhalten(db) -> None:
    """Nach dem ersten Fehlschlag: Automatik für diese Version anhalten (gespeichert)."""
    from app.services.settings_service import SettingsService

    await SettingsService(db).set(ANGEHALTEN, get_agent_version())
    setattr(settings, ANGEHALTEN, get_agent_version())


async def tick(docker, redis) -> dict | None:
    """Einen veralteten Agenten im Leerlauf aktualisieren. Höchstens einen je Aufruf."""
    if modus() == "aus" or docker is None or angehalten():
        return None
    from app.core.agent_manager import AgentManager
    from app.db.session import resilient_session
    from app.models.agent import Agent, AgentState

    async with resilient_session() as db:
        agenten = (await db.execute(select(Agent).where(
            Agent.state.in_((AgentState.RUNNING, AgentState.IDLE)))
            .order_by(Agent.updated_at))).scalars().all()
        kandidat = None
        for agent in agenten:
            if not veraltet(agent) or (agent.config or {}).get(FEHLGESCHLAGEN) == get_agent_version():
                continue
            if await beschaeftigt(db, redis, agent, ruhe=RUHE):
                continue
            kandidat = agent
            break
        if kandidat is None:
            return None
        # Erst das Abbild: ``update_agent`` entfernt den alten Behälter vor dem neuen.
        if not await abbild_bereit(docker):
            logger.warning("[Aktualisieren] Agenten-Abbild %s nicht verfügbar — kein Agent angefasst",
                           settings.agent_image)
            return {"uebersprungen": "abbild_fehlt"}
        # Das Holen kann dauern: unmittelbar davor noch einmal prüfen, ob inzwischen
        # ein Gespräch oder Auftrag begonnen hat.
        if await beschaeftigt(db, redis, kandidat, ruhe=RUHE):
            return None
        # Vor dem Neuerstellen festhalten: nach Commit/Rollback sind die Felder abgelaufen.
        aid, name, von = kandidat.id, kandidat.name, (kandidat.config or {}).get("agent_version")
        try:
            await AgentManager(db, docker, redis).update_agent(aid)
        except Exception as e:  # noqa: BLE001 — ein Agent darf den Takt nicht anhalten
            logger.warning("[Aktualisieren] %s gescheitert, Automatik für %s angehalten: %s",
                           aid, get_agent_version(), e)
            await db.rollback()
            # Merkmal und Anhalten ZUERST festschreiben — scheitert danach das Protokoll,
            # darf das nicht zu einem neuen Versuch alle fünf Minuten führen.
            frisch = await db.get(Agent, aid)
            if frisch is not None:
                frisch.config = {**(frisch.config or {}), FEHLGESCHLAGEN: get_agent_version()}
                flag_modified(frisch, "config")
            await _anhalten(db)
            await db.commit()
            await _protokoll_sicher(db, SimpleNamespace(id=aid), False, str(e))
            return {"gescheitert": aid}
        await _protokoll_sicher(db, SimpleNamespace(id=aid), True)
        logger.info("[Aktualisieren] %s (%s) im Leerlauf aktualisiert: %s → %s",
                    aid, name, von, get_agent_version())
        return {"aktualisiert": aid, "von": von, "nach": get_agent_version()}


async def _protokoll_sicher(db, agent, erfolg: bool, fehler: str = "") -> None:
    """Protokolleintrag in eigener Festschreibung — ein Fehler hier ändert nichts am Ablauf."""
    try:
        await _protokollieren(db, agent, erfolg, fehler)
        await db.commit()
    except Exception:  # noqa: BLE001
        logger.warning("[Aktualisieren] Prüfprotokoll für %s nicht geschrieben", agent.id, exc_info=True)
        await db.rollback()
