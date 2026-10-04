import asyncio
import json

import redis.asyncio as aioredis
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from app.config import settings
from app.telegram._bridge_auth import authed_client
from app.telegram.texte import aufgaben_status, bewertung, kennzahlen, zustand_wort
from app.telegram.active_chats import (
    clear_active_chat,
    get_active_chat,
    set_active_chat,
)

# Use localhost since the bot runs INSIDE the orchestrator container
API_BASE = "http://127.0.0.1:8000/api/v1"

# The chat_id -> agent_id mapping is persisted in Redis (see active_chats.py) so
# it survives orchestrator restarts. Only the per-chat listener tasks stay in
# memory — asyncio.Task objects are process-local and cannot be persisted.
_chat_listeners: dict[int, asyncio.Task] = {}  # chat_id -> listener task


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Auto-save chat ID for notifications
    chat_id = str(update.effective_chat.id)
    if settings.telegram_chat_id != chat_id:
        settings.telegram_chat_id = chat_id

    await update.message.reply_text(
        "*AI Employee Bot*\n\n"
        "Befehle:\n"
        "/status - Zustand der Agenten anzeigen\n"
        "/agents - Alle Agenten auflisten\n"
        "/task <text> - Neue Aufgabe anlegen\n"
        "/chat - Chat mit einem Agenten starten\n"
        "/stop\\_chat - Chat beenden\n\n"
        "Oder schreib einfach eine Nachricht, um mit dem aktiven Agenten zu chatten.",
        parse_mode="Markdown",
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with await authed_client() as client:
        try:
            resp = await client.get(f"{API_BASE}/agents/")
            data = resp.json()

            if not data["agents"]:
                await update.message.reply_text("Keine Agenten aktiv. Lege einen in der Weboberfläche an.")
                return

            lines = ["*Zustand der Agenten:*\n"]
            for agent in data["agents"]:
                task_info = ""
                if agent.get("current_task"):
                    task_info = f"\n  Aufgabe: `{agent['current_task']}`"

                lines.append(
                    f"*{agent['name']}* ({agent['id']})\n"
                    f"  Zustand: {zustand_wort(agent['state'])}{task_info}"
                )

            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(f"Fehler: {e}")


async def cmd_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(
            "Aufruf: /task <beschreibung>\n"
            "Beispiel: /task Erstelle eine Flask-API zum Anlegen und Ändern von Nutzern"
        )
        return

    prompt = " ".join(context.args)

    async with await authed_client() as client:
        try:
            resp = await client.post(
                f"{API_BASE}/tasks/",
                json={
                    "title": prompt[:50],
                    "prompt": prompt,
                    "priority": 1,
                },
            )
            data = resp.json()

            if resp.status_code == 201:
                agent_info = f"Agent: `{data.get('agent_id') or 'wird zugewiesen'}`"
                await update.message.reply_text(
                    f"*Aufgabe angelegt*\n"
                    f"ID: `{data['id']}`\n"
                    f"Status: {aufgaben_status(data['status'])}\n"
                    f"{agent_info}",
                    parse_mode="Markdown",
                )
            else:
                await update.message.reply_text(f"Fehler: {data}")
        except Exception as e:
            await update.message.reply_text(f"Fehler: {e}")


async def cmd_agents(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with await authed_client() as client:
        try:
            resp = await client.get(f"{API_BASE}/agents/")
            data = resp.json()

            if not data["agents"]:
                await update.message.reply_text(
                    "Keine Agenten vorhanden. Lege einen in der Weboberfläche an."
                )
                return

            lines = ["*Agenten:*\n"]
            for agent in data["agents"]:
                cpu = f"{agent.get('cpu_percent', 0):.1f}%" if agent.get("cpu_percent") else "k. A."
                mem = f"{agent.get('memory_usage_mb', 0):.0f}MB" if agent.get("memory_usage_mb") else "k. A."
                lines.append(
                    f"*{agent['name']}* (`{agent['id']}`)\n"
                    f"  Modell: {agent['model']}\n"
                    f"  CPU: {cpu} · RAM: {mem}\n"
                    f"  Warteschlange: {agent.get('queue_depth', 0)} Aufgaben"
                )

            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(f"Fehler: {e}")


async def cmd_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start a chat session with an agent."""
    chat_id = update.effective_chat.id

    # If agent specified as argument, use that
    if context.args:
        agent_id = context.args[0]
        await _start_chat_session(update, chat_id, agent_id)
        return

    # Otherwise show agent selection
    async with await authed_client() as client:
        try:
            resp = await client.get(f"{API_BASE}/agents/")
            data = resp.json()

            if not data["agents"]:
                await update.message.reply_text(
                    "Keine Agenten vorhanden. Lege zuerst einen in der Weboberfläche an."
                )
                return

            # If only one agent, auto-select
            if len(data["agents"]) == 1:
                agent = data["agents"][0]
                await _start_chat_session(update, chat_id, agent["id"])
                return

            # Show inline keyboard for agent selection
            keyboard = [
                [InlineKeyboardButton(
                    f"{a['name']} ({zustand_wort(a['state'])})",
                    callback_data=f"chat:{a['id']}"
                )]
                for a in data["agents"]
                if a["state"] in ("running", "idle", "working")
            ]

            if not keyboard:
                await update.message.reply_text("Keine laufenden Agenten gefunden.")
                return

            await update.message.reply_text(
                "Wähle einen Agenten zum Chatten:",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        except Exception as e:
            await update.message.reply_text(f"Fehler: {e}")


async def cmd_stop_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stop the current chat session."""
    chat_id = update.effective_chat.id

    agent_id = await clear_active_chat(chat_id)
    if agent_id:
        # Cancel listener
        if chat_id in _chat_listeners:
            _chat_listeners[chat_id].cancel()
            del _chat_listeners[chat_id]
        await update.message.reply_text(
            f"Chat mit Agent `{agent_id}` beendet.",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text("Kein aktiver Chat.")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline keyboard callbacks."""
    query = update.callback_query
    await query.answer()

    data = query.data
    if data.startswith("chat:"):
        agent_id = data.split(":", 1)[1]
        chat_id = query.message.chat_id
        await _start_chat_session(update, chat_id, agent_id, callback_query=query)
    elif data.startswith("rate:"):
        await _handle_rating_callback(query, data)


async def _handle_rating_callback(query, data: str) -> None:
    """Handle task rating from inline keyboard (rate:task_id:1-5)."""
    try:
        parts = data.split(":")
        if len(parts) != 3:
            await query.edit_message_text("Ungültige Bewertung.")
            return

        task_id = parts[1]
        rating = int(parts[2])
        if rating < 1 or rating > 5:
            await query.edit_message_text("Die Bewertung muss zwischen 1 und 5 liegen.")
            return

        # Save rating via internal API (authed_client attaches the admin Bearer JWT;
        # no spoofable X-Internal header needed — the endpoint authenticates the JWT).
        async with await authed_client() as client:
            resp = await client.post(
                f"{API_BASE}/ratings/tasks/{task_id}/rate",
                json={"rating": rating},
                timeout=10.0,
            )

        if resp.status_code in (200, 201):
            await query.edit_message_text(
                f"Danke für deine Bewertung: {bewertung(rating)}."
            )
        elif resp.status_code == 409:
            await query.edit_message_text("Du hast diese Aufgabe bereits bewertet.")
        else:
            await query.edit_message_text(
                f"Bewertung {bewertung(rating)} konnte nicht gespeichert werden "
                f"(Fehler {resp.status_code})."
            )
    except Exception as e:
        await query.edit_message_text(f"Fehler beim Bewerten: {e}")


def _ensure_listener(bot, chat_id: int, agent_id: str, restart: bool = False) -> None:
    """Ensure a response listener task is running for ``chat_id``.

    ``restart=True`` always (re)creates the task (used by /chat). Otherwise the
    listener is only started when none is currently running for this chat — the
    lazy path that re-establishes the process-local task after a restart, since
    only the chat mapping (not the asyncio.Task) survives in Redis.
    """
    existing = _chat_listeners.get(chat_id)
    alive = existing is not None and not existing.done()
    if alive and not restart:
        return
    if existing is not None:
        existing.cancel()
    _chat_listeners[chat_id] = asyncio.create_task(
        _listen_agent_responses(bot, chat_id, agent_id)
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle regular text messages - forward to active chat agent."""
    chat_id = update.effective_chat.id

    agent_id = await get_active_chat(chat_id)
    if agent_id is None:
        # No active chat - show hint
        await update.message.reply_text(
            "Kein aktiver Chat. Starte mit /chat einen Chat mit einem Agenten."
        )
        return

    # The chat mapping is persisted in Redis (survives restarts) but the response
    # listener is a process-local asyncio.Task that is lost on restart. Re-establish
    # it here, otherwise the agent's reply is published to Redis but never forwarded
    # to Telegram — the bot would accept messages and stay silent (#408).
    _ensure_listener(update.message.get_bot(), chat_id, agent_id)

    text = update.message.text

    # Send to agent via Redis (use internal Docker hostname)
    try:
        redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        message_id = f"tg-{update.message.message_id}"
        payload = {
            "id": message_id,
            "text": text,
            "model": None,
            "source": "telegram",
            "telegram": {
                "chat_id": chat_id,
                "username": update.effective_user.username if update.effective_user else "",
                "first_name": update.effective_user.first_name if update.effective_user else "",
            },
        }
        # Budget (#898): gesperrt → der Lauscher oben bekommt den Hinweis als ``error``.
        from app.core.chat_auftrag import einreihen

        eingereiht = (await einreihen(redis, agent_id, payload)).eingereiht
        await redis.aclose()
        if not eingereiht:
            return

        # Show typing indicator
        await update.effective_chat.send_action("typing")

    except Exception as e:
        await update.message.reply_text(f"Fehler beim Senden: {e}")


async def _start_chat_session(
    update: Update,
    chat_id: int,
    agent_id: str,
    callback_query=None,
) -> None:
    """Start a chat session with an agent and set up response listener."""
    await set_active_chat(chat_id, agent_id)

    # (Re)start the response listener for this chat.
    bot = update.get_bot() if callback_query else update.message.get_bot()
    _ensure_listener(bot, chat_id, agent_id, restart=True)

    msg = (
        f"Chat mit Agent `{agent_id}` gestartet.\n\n"
        f"Schreib einfach eine Nachricht.\n"
        f"Beenden mit /stop\\_chat"
    )

    if callback_query:
        await callback_query.edit_message_text(msg, parse_mode="Markdown")
    else:
        await update.message.reply_text(msg, parse_mode="Markdown")


async def _listen_agent_responses(bot, chat_id: int, agent_id: str) -> None:
    """Background task: listen to agent chat responses and forward to Telegram."""
    try:
        redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        pubsub = redis.pubsub()
        await pubsub.subscribe(f"agent:{agent_id}:chat:response")

        response_buffer = ""

        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1.0
            )
            if message and message["type"] == "message":
                data = json.loads(message["data"])
                event_type = data.get("type", "")
                event_data = data.get("data", {})

                if event_type == "text":
                    response_buffer += str(event_data.get("text", ""))

                elif event_type == "tool_call":
                    tool = event_data.get("tool", "unbekannt")
                    tool_input = json.dumps(event_data.get("input", {}))[:200]
                    response_buffer += f"\nWerkzeug [{tool}] {tool_input}\n"

                elif event_type == "error":
                    error_msg = str(event_data.get("message", "Unbekannter Fehler"))
                    await bot.send_message(
                        chat_id=chat_id,
                        text=f"Fehler: {error_msg}",
                    )
                    response_buffer = ""

                elif event_type == "done":
                    if response_buffer.strip():
                        # Telegram has a 4096 char limit per message
                        text = response_buffer.strip()
                        for i in range(0, len(text), 4000):
                            chunk = text[i:i + 4000]
                            await bot.send_message(
                                chat_id=chat_id,
                                text=chunk,
                            )
                    response_buffer = ""

                    # Kennzahlen des Laufs: „Dauer 3,2 s · Kosten 0,01 $ · 2 Runden“
                    meta = kennzahlen(
                        event_data.get("duration_ms", 0),
                        event_data.get("cost_usd", 0),
                        event_data.get("num_turns", 0),
                    )
                    if meta:
                        await bot.send_message(
                            chat_id=chat_id,
                            text=meta,
                        )

            await asyncio.sleep(0.05)

    except asyncio.CancelledError:
        pass
    except Exception:
        pass
    finally:
        try:
            await pubsub.unsubscribe()
            await pubsub.aclose()
            await redis.aclose()
        except Exception:
            pass
