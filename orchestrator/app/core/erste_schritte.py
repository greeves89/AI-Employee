"""Erste Schritte: fünf Schritte, mit denen ein Mensch AI-Employee sauber nutzt (#1047).

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


async def _werkzeuge(db: AsyncSession, uid: str, redis) -> bool:
    if await db.scalar(select(exists().where(
            OAuthIntegration.provider.in_(_WERKZEUGE),
            or_(OAuthIntegration.user_id == uid, OAuthIntegration.user_id.is_(None))))):
        return True
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
    """Die fünf Schritte für diesen Nutzer, mit Beleg aus dem System."""
    uid = str(user.id)
    admin = _ist_admin(user)
    schritte = [
        {
            "id": "ki_zugang",
            "titel": "KI-Zugang verbinden",
            "text": ("Hinterlege ein Claude-, ChatGPT- oder Azure-Konto. Ohne Zugang kann kein "
                     "Agent denken; ein Konto reicht für alle Agenten."),
            "link": "/ai-accounts" if admin else "/settings",
            "erledigt": await _ki_zugang(db, user),
            # Für Mitglieder richtet das meist die Verwaltung ein.
            "hinweis": None if admin else "Das richtet meist deine Verwaltung ein.",
        },
        {
            "id": "agent",
            "titel": "Agenten aus einer Vorlage anlegen",
            "text": ("Wähle eine Vorlage, gib ihm einen Namen und eine Aufgabe in einem Satz. "
                     "Am Anfang fragt er, bevor er etwas Folgenreiches tut."),
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
            "id": "werkzeuge",
            "titel": "Werkzeuge anschließen",
            "text": ("Verbinde Mail und Kalender oder installiere die Bridge, damit er auf deinem "
                     "Rechner mithilft. Erst dann erledigt er Dinge, statt nur zu antworten."),
            "link": "/integrations",
            "erledigt": await _werkzeuge(db, uid, redis),
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
    ]
    erledigt = sum(1 for s in schritte if s["erledigt"])
    return {"schritte": schritte, "erledigt": erledigt, "gesamt": len(schritte)}
