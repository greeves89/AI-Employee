"""Erste Schritte: die Schritte, mit denen ein Mensch AI-Employee sauber nutzt (#1047).

Jeder Schritt gilt als erledigt, wenn es im System den Beleg dafür gibt — nicht,
weil jemand ein Häkchen gesetzt hat. So bleibt die Liste ehrlich: wer seinen
einzigen Agenten löscht, sieht Schritt 2 wieder offen.

Alles hier gilt für den EINEN anfragenden Nutzer. Die bestehenden
„sichtbar“-Helfer liefern Admins absichtlich alles; hier zählt nur Eigenes, sonst
wäre die Liste für jeden Admin vom ersten Tag an „fertig“.
"""

from __future__ import annotations

import logging

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.ai_account import AIAccount
from app.models.device_token import DeviceToken
from app.models.knowledge import KnowledgeEntry
from app.models.memory import AgentMemory
from app.models.oauth_integration import OAuthIntegration, OAuthProvider
from app.models.schedule import Schedule
from app.models.user_ai_credential import UserAiCredential

logger = logging.getLogger(__name__)

# Merker „Bridge war schon einmal verbunden“ — Sitzungen selbst laufen nach 7 Tagen ab.
BRIDGE_MERKER = "computer_use:bridge_jemals:{user_id}"

# Anbindungen, die Werkzeuge sind (nicht KI-Zugänge wie anthropic/codex).
_WERKZEUGE = (OAuthProvider.MICROSOFT, OAuthProvider.GOOGLE, OAuthProvider.EXCHANGE_ONPREM,
              OAuthProvider.GITHUB)


def _ist_admin(user) -> bool:
    from app.core.ownership import is_admin
    return is_admin(user)


async def _ki_zugang(db: AsyncSession, user) -> bool:
    from app.core import agent_credentials as ac
    uid = str(user.id)
    if ac.team_license_allowed() and await ac.team_secret(db, "claude_code"):
        return True
    if ac.personal_credentials_allowed(user):
        eigen = await db.scalar(select(exists().where(
            UserAiCredential.user_id == uid,
            UserAiCredential.secret_encrypted.is_not(None),
            or_(UserAiCredential.last_status.is_(None), UserAiCredential.last_status != "auth_failed"),
        )))
        if eigen:
            return True
    from app.api.ai_accounts import _allowed_account_ids
    erlaubt = await _allowed_account_ids(user, db)
    abfrage = select(exists().where(AIAccount.is_active == True))  # noqa: E712
    if erlaubt is not None:
        if not erlaubt:
            return False
        abfrage = select(exists().where(AIAccount.is_active == True, AIAccount.id.in_(erlaubt)))  # noqa: E712
    return bool(await db.scalar(abfrage))


def _eigene_agenten(uid: str):
    return select(Agent.id).where(Agent.user_id == uid)


async def _agent(db: AsyncSession, uid: str) -> bool:
    return bool(await db.scalar(select(exists().where(Agent.user_id == uid))))


async def _gedaechtnis(db: AsyncSession, uid: str) -> bool:
    """Hat einer SEINER Agenten sich schon etwas gemerkt?"""
    return bool(await db.scalar(select(exists().where(AgentMemory.agent_id.in_(_eigene_agenten(uid))))))


async def _wissen(db: AsyncSession, uid: str) -> bool:
    if await db.scalar(select(exists().where(KnowledgeEntry.user_id == uid))):
        return True
    return await _gedaechtnis(db, uid)


async def _mail_kalender(db: AsyncSession, uid: str) -> bool:
    """Eigene oder von der Verwaltung geteilte Anbindung (Microsoft 365, Google, Exchange, GitHub)."""
    return bool(await db.scalar(select(exists().where(
        OAuthIntegration.provider.in_(_WERKZEUGE),
        or_(OAuthIntegration.user_id == uid, OAuthIntegration.user_id.is_(None))))))


async def _iphone(db: AsyncSession, uid: str) -> bool:
    """Ein iPhone mit der App hat sich für Benachrichtigungen angemeldet."""
    return bool(await db.scalar(select(exists().where(DeviceToken.user_id == uid))))


async def _bridge(uid: str, redis) -> bool:
    client = getattr(redis, "client", None)
    if client is None:
        return False
    try:
        return bool(await client.exists(BRIDGE_MERKER.format(user_id=uid)))
    except Exception:  # noqa: BLE001 — Redis weg: lieber „offen“ als ein Fehler auf der Startseite
        logger.debug("Bridge-Merker nicht lesbar", exc_info=True)
        return False


async def _alltag(db: AsyncSession, user) -> bool:
    uid = str(user.id)
    if await db.scalar(select(exists().where(
            Schedule.enabled == True,  # noqa: E712
            Schedule.agent_id.in_(_eigene_agenten(uid))))):
        return True
    from app.core import sekretariat
    return await sekretariat.von_nutzer(db, user) is not None


async def stand(db: AsyncSession, user, redis=None) -> dict:
    """Die Einstiegsschritte für diesen Nutzer, mit Beleg aus dem System."""
    uid = str(user.id)
    admin = _ist_admin(user)
    ki = await _ki_zugang(db, user)
    schritte = [
        {
            "id": "ki_zugang",
            "titel": "KI-Zugang verbinden",
            "text": ("Hinterlege ein Claude-, ChatGPT- oder Azure-Konto. Ohne Zugang kann kein "
                     "Agent denken; ein Konto reicht für alle Agenten."),
            "link": "/ai-accounts" if admin else "/settings",
            "erledigt": ki,
            # Für Mitglieder richtet das meist die Verwaltung ein.
            "hinweis": None if admin else "Das richtet meist deine Verwaltung ein.",
        },
        {
            "id": "agent",
            "titel": "Agenten aus einer Vorlage anlegen",
            "text": ("Wähle eine Vorlage, gib ihm einen Namen und sag in einem Satz, wobei er dir "
                     "helfen soll. Am Anfang fragt er, bevor er etwas Wichtiges tut."),
            "link": "/onboarding" if admin else "/agents",
            "erledigt": await _agent(db, uid),
            "hinweis": None,
        },
        {
            "id": "wissen",
            "titel": "Wissen geben",
            "text": ("Gib ihm, was ein neuer Kollege am ersten Tag bekäme: Abläufe, Vorlagen, "
                     "wichtige Kontakte. Er schlägt dort nach, statt zu raten."),
            "link": "/knowledge",
            "erledigt": await _wissen(db, uid),
            "hinweis": None,
        },
        {
            "id": "mail_kalender",
            "titel": "Mail und Kalender verbinden",
            "text": ("Melde dich einmal mit deinem Microsoft- oder Google-Konto an. Dann kann er "
                     "Mails vorbereiten und Termine im Blick behalten."),
            "link": "/integrations",
            "erledigt": await _mail_kalender(db, uid),
            "hinweis": None,
        },
        {
            "id": "alltag",
            "titel": "In den Alltag holen",
            "text": ("Gib ihm einen festen Rhythmus, etwa morgens den Tag vorbereiten, und schau "
                     "einmal am Tag auf „Heute“ und in die Freigaben."),
            "link": "/schedules",
            "erledigt": await _alltag(db, user),
            "hinweis": None,
        },
        {
            "id": "iphone_app",
            "titel": "App aufs iPhone holen",
            "text": ("Lade „AI Employee Hub“ aus dem App Store und melde dich an. Dann sprichst du "
                     "unterwegs mit deinem Agenten und gibst Freigaben direkt am Handy."),
            "link": "",          # die Oberfläche zeigt hier QR-Code und App-Store-Link
            "erledigt": await _iphone(db, uid),
            "hinweis": None,
        },
        {
            # Zuletzt: der große Schritt — der Agent hilft direkt auf dem eigenen Rechner.
            "id": "bridge",
            "titel": "Auf deinem Rechner mithelfen lassen",
            "text": ("Installiere die Bridge auf deinem Mac oder Windows-PC. Dann sieht dein Agent, "
                     "woran du gerade arbeitest, bietet Hilfe an und fragt, bevor er etwas tut."),
            "link": "",          # die Oberfläche zeigt hier direkt die Downloads
            "erledigt": await _bridge(uid, redis),
            "hinweis": None,
        },
    ]
    # Den KI-Zugang richtet bei Mitgliedern die Verwaltung ein: ist er da, gehört er
    # nicht in ihre Liste. Fehlt er, bleibt er sichtbar — ohne ihn geht nichts.
    if not admin and ki:
        schritte = [s for s in schritte if s["id"] != "ki_zugang"]
    erledigt = sum(1 for s in schritte if s["erledigt"])
    return {"schritte": schritte, "erledigt": erledigt, "gesamt": len(schritte)}
