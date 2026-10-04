"""Aufbewahrungsfristen für Prüfprotokoll und Chatverläufe (#892).

Bis hierhin wuchsen ``audit_logs`` und ``chat_messages`` unbegrenzt — eine Frist
ließ sich nirgends einstellen. Jetzt legt ein Administrator unter Admin → Betrieb
je eine Frist in Tagen fest; leer oder 0 heißt unbegrenzt (Voreinstellung, damit
ein Update nichts ungefragt löscht).

Ein Aufräumlauf am Tag. Er hängt am Takt des Schedulers (``scheduler_service``,
Fünf-Minuten-Block neben Nachtschicht und Wochensynthese) und merkt sich den
letzten Lauf in den Einstellungen — ein Neustart löst also keinen zweiten aus.

Gelöscht wird in Stapeln mit Festschreiben dazwischen: eine große Anlage mit
Hunderttausenden Zeilen hält sonst eine einzige lange Transaktion offen.

Jeder Lauf mit Löschungen schreibt EINEN Eintrag ins Prüfprotokoll (Anzahl,
Frist). Er entsteht NACH dem Löschen mit dem Zeitpunkt des Laufs — die Grenze
liegt mindestens ``MINDEST_TAGE_AUDIT`` Tage zurück, also kann er nicht im selben
Lauf mitgelöscht werden.

Nutzertrennung spielt hier keine Rolle: die Frist gilt anlagenweit.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, exists, select

from app.models.audit_log import AuditEventType, AuditLog
from app.models.chat_message import ChatMessage
from app.models.chat_session import ChatSession

logger = logging.getLogger(__name__)

SCHLUESSEL_AUDIT = "retention_audit_days"
SCHLUESSEL_CHAT = "retention_chat_days"
SCHLUESSEL_LETZTER_LAUF = "retention_last_run"

#: Untergrenzen. Das Prüfprotokoll soll niemand versehentlich leeren können;
#: bei Chats schützt die Woche laufende Gespräche vor einem Tippfehler.
MINDEST_TAGE_AUDIT = 30
MINDEST_TAGE_CHAT = 7
#: Obergrenze nur gegen Unsinn (Überlauf beim Rechnen mit Datumswerten).
HOECHST_TAGE = 36500

#: Zeilen je Stapel und höchstens so viele Stapel je Tabelle und Lauf — der Rest
#: kommt am nächsten Tag dran, statt den Scheduler lange zu blockieren.
STAPEL = 1000
MAX_STAPEL_JE_LAUF = 500

#: Mindestabstand zwischen zwei Läufen (wie der Trend-Scan: „einmal am Tag“).
TAKT = timedelta(hours=23)


def tage_lesen(roh: str | None) -> int | None:
    """Gespeicherten Wert deuten. Leer, 0, negativ oder unlesbar → unbegrenzt."""
    try:
        tage = int(str(roh or "").strip() or 0)
    except ValueError:
        logger.warning("Aufbewahrungsfrist unlesbar (%r) — gilt als unbegrenzt", roh)
        return None
    return tage if tage > 0 else None


def frist_pruefen(tage: int | None, mindest: int, was: str) -> int | None:
    """Eingabe aus der Oberfläche prüfen. ``None``/0 = unbegrenzt.

    Wirft ``ValueError`` mit einer Meldung für die Oberfläche.
    """
    if tage is None or tage == 0:
        return None
    if tage < mindest:
        raise ValueError(f"{was}: mindestens {mindest} Tage (oder leer für unbegrenzt).")
    if tage > HOECHST_TAGE:
        raise ValueError(f"{was}: höchstens {HOECHST_TAGE} Tage.")
    return tage


async def fristen(svc) -> tuple[int | None, int | None]:
    """Wirksame Fristen (Prüfprotokoll, Chat) — unter der Untergrenze angehoben.

    Die Oberfläche lässt zu kurze Fristen gar nicht erst durch; wer am Weg vorbei
    einen kleineren Wert in die Datenbank schreibt, bekommt trotzdem die Untergrenze.
    """
    audit = tage_lesen(await svc.get(SCHLUESSEL_AUDIT))
    chat = tage_lesen(await svc.get(SCHLUESSEL_CHAT))
    if audit is not None:
        audit = min(max(audit, MINDEST_TAGE_AUDIT), HOECHST_TAGE)
    if chat is not None:
        chat = min(max(chat, MINDEST_TAGE_CHAT), HOECHST_TAGE)
    return audit, chat


async def letzter_lauf(svc) -> dict:
    roh = await svc.get(SCHLUESSEL_LETZTER_LAUF)
    if not roh:
        return {}
    try:
        stand = json.loads(roh)
    except ValueError:
        return {}
    return stand if isinstance(stand, dict) else {}


def _zeit(wert) -> datetime | None:
    try:
        zeit = datetime.fromisoformat(str(wert))
    except (TypeError, ValueError):
        return None
    return zeit if zeit.tzinfo else zeit.replace(tzinfo=timezone.utc)


async def _in_stapeln_loeschen(db, modell, spalte, grenze: datetime) -> int:
    """Zeilen älter als ``grenze`` löschen, Stapel für Stapel festgeschrieben."""
    geloescht = 0
    for _ in range(MAX_STAPEL_JE_LAUF):
        ids = (await db.execute(
            select(modell.id).where(spalte < grenze).order_by(modell.id).limit(STAPEL)
        )).scalars().all()
        if not ids:
            break
        await db.execute(delete(modell).where(modell.id.in_(ids)))
        await db.commit()
        geloescht += len(ids)
    return geloescht


async def _leere_gespraeche_loeschen(db, grenze: datetime) -> int:
    """Gesprächs-Metadaten (Titel, Anheften) ohne verbliebene Nachricht.

    Nur wenn sie selbst seit der Frist nicht mehr angefasst wurden — ein frisch
    angelegtes, noch leeres Gespräch bleibt.
    """
    hat_nachricht = exists().where(
        ChatMessage.agent_id == ChatSession.agent_id,
        ChatMessage.session_id == ChatSession.session_id,
    )
    ergebnis = await db.execute(
        delete(ChatSession).where(ChatSession.updated_at < grenze, ~hat_nachricht)
    )
    await db.commit()
    return ergebnis.rowcount or 0


async def aufraeumen(db, svc, now: datetime | None = None) -> dict:
    """Ein Aufräumlauf. Schreibt selbst fest (stapelweise)."""
    from app.core.audit import AKTEUR_SYSTEM, protokolliere

    now = now or datetime.now(timezone.utc)
    audit_tage, chat_tage = await fristen(svc)
    ergebnis: dict = {"zeit": now.isoformat(), "audit_frist_tage": audit_tage,
                      "chat_frist_tage": chat_tage, "audit_geloescht": 0,
                      "chat_nachrichten_geloescht": 0, "chat_sitzungen_geloescht": 0}

    if audit_tage is not None:
        ergebnis["audit_geloescht"] = await _in_stapeln_loeschen(
            db, AuditLog, AuditLog.created_at, now - timedelta(days=audit_tage))
    if chat_tage is not None:
        grenze = now - timedelta(days=chat_tage)
        ergebnis["chat_nachrichten_geloescht"] = await _in_stapeln_loeschen(
            db, ChatMessage, ChatMessage.timestamp, grenze)
        ergebnis["chat_sitzungen_geloescht"] = await _leere_gespraeche_loeschen(db, grenze)

    teile = []
    if ergebnis["audit_geloescht"]:
        teile.append(f"{ergebnis['audit_geloescht']} Protokolleinträge (älter als {audit_tage} Tage)")
    if ergebnis["chat_nachrichten_geloescht"] or ergebnis["chat_sitzungen_geloescht"]:
        teile.append(f"{ergebnis['chat_nachrichten_geloescht']} Chatnachrichten (älter als {chat_tage} Tage)")
    if teile:
        # NACH dem Löschen und mit dem Zeitpunkt des Laufs: dieser Eintrag ist
        # jünger als jede Grenze und bleibt stehen.
        eintrag = await protokolliere(
            db, AuditEventType.RETENTION_PURGED, agent_id=AKTEUR_SYSTEM,
            command="Aufbewahrungsfrist angewendet: " + ", ".join(teile) + " gelöscht",
            meta={k: v for k, v in ergebnis.items() if k != "zeit"},
        )
        if eintrag is not None:
            eintrag.created_at = now

    await svc.set(SCHLUESSEL_LETZTER_LAUF, json.dumps(ergebnis))
    await db.commit()
    if teile:
        logger.info("[Aufbewahrung] %s", "; ".join(teile))
    return ergebnis


async def tick(session_factory=None, now: datetime | None = None) -> dict | None:
    """Vom Scheduler alle paar Minuten gerufen; läuft höchstens einmal am Tag.

    Ohne eingestellte Frist kostet das zwei Einstellungs-Lesezugriffe.
    """
    from app.db.session import resilient_session
    from app.services.settings_service import SettingsService

    now = now or datetime.now(timezone.utc)
    async with resilient_session(session_factory=session_factory) as db:
        svc = SettingsService(db)
        audit_tage, chat_tage = await fristen(svc)
        if audit_tage is None and chat_tage is None:
            return None
        zuletzt = _zeit((await letzter_lauf(svc)).get("zeit"))
        if zuletzt and now - zuletzt < TAKT:
            return None
        return await aufraeumen(db, svc, now)
