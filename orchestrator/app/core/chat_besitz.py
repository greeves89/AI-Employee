"""Wem gehört ein Gespräch? Chats je Nutzer an freigegebenen Agenten.

Ein Agent kann mehreren Menschen freigegeben sein (``AgentAccess``) — etwa ein
Assistenz-Agent für eine ganze Abteilung. Wer mit ihm schreibt, sieht dann nur
SEINE Gespräche, nicht die der Kollegen.

Regeln (eine Stelle, alle Schnittstellen fragen hier):

* Besitzer des Agenten und Admins sehen alle Gespräche des Agenten. Der Agent
  selbst (Agenten-Token) ebenfalls — es ist sein eigener Verlauf.
* Alle anderen (freigegebene Nutzer, auch Manager) sehen nur Gespräche, die in
  ``chat_sessions.user_id`` auf sie eingetragen sind.
* Ein Gespräch ohne eingetragenen Nutzer gehört dem Besitzer des Agenten: alte
  Gespräche von vor dieser Regel, Zeitpläne, Telegram, Bridge, Aufgaben.
* Eingetragen wird beim ersten Schreiben in ein NEUES Gespräch
  (``beanspruchen``). Ein Gespräch mit Nachrichten wechselt nie den Besitzer.

Der Agent selbst kennt alle Gespräche (Gedächtnis, Dateien) — getrennt wird die
Ansicht und das Weiterschreiben, nicht das Wissen des Agenten.
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat_message import ChatMessage
from app.models.chat_session import ChatSession


#: Gespraeche, die das System selbst anlegt: Zeitplaene, Meldungen, Bridge. Sie gehoeren
#: immer dem Besitzer des Agenten — auch solange sie noch leer sind (sonst koennte ein
#: freigegebener Nutzer die Kennung vorab fuer sich beanspruchen).
SYSTEM_GESPRAECHE = frozenset({"scheduler", "meldungen", "bridge", "bridge-beobachter"})
SYSTEM_PRAEFIXE = ("zeitplan-",)


def vom_system(session_id: str) -> bool:
    """Legt das System dieses Gespraech an (Zeitplan, Kanal wie ``telegram:…``)?"""
    sid = str(session_id or "")
    return sid in SYSTEM_GESPRAECHE or sid.startswith(SYSTEM_PRAEFIXE) or ":" in sid


async def sieht_alle(db: AsyncSession, agent_id: str, user) -> bool:
    """Darf ``user`` ALLE Gespräche dieses Agenten sehen?"""
    from app.dependencies import is_agent_principal
    from app.models.agent import Agent
    from app.models.user import UserRole

    if is_agent_principal(user):
        return getattr(user, "id", None) == agent_id
    if getattr(user, "role", None) == UserRole.ADMIN:
        return True
    besitzer = await db.scalar(select(Agent.user_id).where(Agent.id == agent_id))
    return bool(besitzer) and besitzer == getattr(user, "id", None)


async def eigene_sitzungen(db: AsyncSession, agent_id: str, user) -> set[str] | None:
    """Gesprächskennungen, die ``user`` sehen darf — ``None`` heißt: alle."""
    if await sieht_alle(db, agent_id, user):
        return None
    return set((await db.execute(
        select(ChatSession.session_id).where(
            ChatSession.agent_id == agent_id, ChatSession.user_id == user.id)
    )).scalars().all())


async def _hat_nachrichten(db: AsyncSession, agent_id: str, session_id: str) -> bool:
    return (await db.scalar(
        select(ChatMessage.id).where(
            ChatMessage.agent_id == agent_id, ChatMessage.session_id == session_id).limit(1)
    )) is not None


async def darf(db: AsyncSession, agent_id: str, user, session_id: str) -> bool:
    """Darf ``user`` dieses Gespräch sehen und darin weiterschreiben?

    Frei ist nur eine Kennung, zu der es noch NICHTS gibt — weder Zeile noch
    Nachrichten: Genau so beginnt jedes neue Gespräch. Eine Zeile ohne Besitzer
    (angepinnt, umbenannt, vom System angelegt) gehört dem Besitzer des Agenten."""
    if not isinstance(session_id, str) or not session_id:
        return False
    if await sieht_alle(db, agent_id, user):
        return True
    if vom_system(session_id):
        return False
    zeile = await db.scalar(select(ChatSession).where(
        ChatSession.agent_id == agent_id, ChatSession.session_id == session_id))
    if zeile is not None:
        return bool(zeile.user_id) and zeile.user_id == user.id
    return not await _hat_nachrichten(db, agent_id, session_id)


async def pruefen(db: AsyncSession, agent_id: str, user, session_id: str) -> None:
    """Wie ``darf`` — wirft 404, damit fremde Gesprächskennungen nicht einmal als
    vorhanden erkennbar sind."""
    if not await darf(db, agent_id, user, session_id):
        raise HTTPException(status_code=404, detail="Gespräch nicht gefunden")


async def beanspruchen(db: AsyncSession, agent_id: str, session_id: str, user_id: str | None) -> None:
    """Ein NEUES Gespräch dem Nutzer zuschreiben, der darin zuerst schreibt.

    Ändert nichts an Gesprächen, die schon eine Zeile oder Nachrichten haben, und
    nichts an Gesprächen des Systems.
    Committet selbst (wird vor dem Speichern der ersten Nachricht aufgerufen)."""
    if not user_id or user_id == "unknown" or not isinstance(session_id, str) or not session_id:
        return
    if vom_system(session_id):
        return
    zeile = await db.scalar(select(ChatSession).where(
        ChatSession.agent_id == agent_id, ChatSession.session_id == session_id))
    if zeile is not None or await _hat_nachrichten(db, agent_id, session_id):
        return
    db.add(ChatSession(agent_id=agent_id, session_id=session_id, user_id=user_id))
    try:
        await db.commit()
    except IntegrityError:
        # Zwei Fenster legen dasselbe neue Gespräch gleichzeitig an: die Zeile
        # steht jetzt, der Besitzer ist der erste.
        await db.rollback()


async def besitzer_von(db: AsyncSession, agent_id: str, session_id: str) -> str | None:
    """Eingetragener Besitzer eines Gesprächs — für Kopien (Abzweig, Sicherung,
    Fortsetzung), die dem Besitzer des Originals gehören."""
    return await db.scalar(select(ChatSession.user_id).where(
        ChatSession.agent_id == agent_id, ChatSession.session_id == session_id))
