"""Welche Nachricht steht in einem Telegram-Chat gerade ganz unten?

Die Antwort des Agenten erscheint in EINER Nachricht, die mitwaechst
(``AgentBot._live_update``): einmal gesendet, danach nur noch bearbeitet. Eine
bearbeitete Nachricht bleibt aber an ihrer Stelle im Verlauf stehen. Kommt
darunter etwas anderes an — der Nutzer schreibt nach, oder der Agent schickt
waehrenddessen selbst Nachrichten ueber die Werkzeug-API (send-message,
send-photo-upload …) —, dann waechst die Antwort weiter OBEN, wo niemand
hinsieht. Unten erscheint am Ende nur die Fusszeile „⏱ 7.7s | 🔄 1 turns".

Gemeldeter Fall (Pi-Anlage, 2026-09-27): Der Agent schickt einen Artikel in
sechs Teilen per send-message, der Nutzer fragt „bist du noch da?", der Agent
antwortet „Ja, bin da! … Soll ich …?" — diese Antwort landete in der
Live-Nachricht UEBER den sechs Teilen. Sichtbar war nur die Fusszeile; der
Agent wartete auf eine Antwort, die der Nutzer nie gesehen hatte.

Deshalb merkt sich dieses Modul je Bot und Chat die juengste bekannte
Nachrichten-ID. Telegram zaehlt die IDs in einem Chat fortlaufend, fuer
Nachrichten in beide Richtungen — ist eine neuere bekannt, steht die
Live-Nachricht nicht mehr unten.

Speicher: ein Sorted Set je Bot (Mitglied = chat_id, Score = message_id).
``ZADD … GT`` haelt das Maximum atomar, auch wenn Orchestrator-Endpunkte und
Bot gleichzeitig schreiben. Der Schluessel enthaelt die Bot-ID, weil chat_id
in Privatchats die Nutzer-ID ist — derselbe Nutzer hat mit jedem Agenten-Bot
einen eigenen Chat mit eigener Zaehlung.

Alles hier ist best effort: Ein Redis-Fehler darf nie eine Nachricht
verhindern. Im Zweifel gilt die Live-Nachricht als „unten" — das ist genau das
Verhalten von vorher.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

import redis.asyncio as aioredis

from app.config import settings

logger = logging.getLogger(__name__)

_KEY = "telegram:chat_tail:{bot_id}"


def bot_id_from_token(token: str | None) -> str:
    """Die Bot-ID ist der Teil des BotFather-Tokens vor dem Doppelpunkt."""
    return (token or "").split(":", 1)[0]


def _client():
    return aioredis.from_url(settings.redis_url, decode_responses=True)


def sent_message_ids(result: Any) -> list[tuple[int, int]]:
    """(chat_id, message_id) aus einer Bot-API-Antwort.

    sendMessage/sendPhoto/… liefern eine Message, sendMediaGroup eine Liste
    davon; alles andere (True, leere Antwort) ergibt nichts.
    """
    items: Iterable[Any] = result if isinstance(result, list) else [result]
    found: list[tuple[int, int]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        chat = item.get("chat") or {}
        chat_id, message_id = chat.get("id"), item.get("message_id")
        if isinstance(chat_id, int) and isinstance(message_id, int):
            found.append((chat_id, message_id))
    return found


async def note(bot_id: str, chat_id: int | str, message_id: int | str) -> None:
    """``message_id`` als juengste Nachricht merken — nur, wenn sie neuer ist."""
    if not bot_id or not chat_id or not message_id:
        return
    r = _client()
    try:
        await r.zadd(_KEY.format(bot_id=bot_id), {str(chat_id): int(message_id)}, gt=True)
    except Exception as e:  # noqa: BLE001 — nie das Senden stoeren
        logger.debug("[Telegram] chat_tail.note fehlgeschlagen: %s", e)
    finally:
        try:
            await r.aclose()
        except Exception:  # noqa: BLE001
            pass


async def newest(bot_id: str, chat_id: int | str) -> int:
    """Juengste bekannte Nachrichten-ID im Chat, 0 wenn unbekannt."""
    if not bot_id or not chat_id:
        return 0
    r = _client()
    try:
        score = await r.zscore(_KEY.format(bot_id=bot_id), str(chat_id))
        return int(score) if score is not None else 0
    except Exception as e:  # noqa: BLE001
        logger.debug("[Telegram] chat_tail.newest fehlgeschlagen: %s", e)
        return 0
    finally:
        try:
            await r.aclose()
        except Exception:  # noqa: BLE001
            pass


async def is_buried(bot_id: str, chat_id: int | str, message_id: int | None) -> bool:
    """True, wenn im Chat schon eine NEUERE Nachricht als ``message_id`` steht."""
    if not message_id:
        return False
    return await newest(bot_id, chat_id) > int(message_id)
