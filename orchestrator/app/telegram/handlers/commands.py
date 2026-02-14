import asyncio
import json

import httpx
import redis.asyncio as aioredis
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from app.config import settings

# Use localhost since the bot runs INSIDE the orchestrator container
API_BASE = "http://127.0.0.1:8000/api/v1"

# Track which Telegram user chats with which agent
_active_chats: dict[int, str] = {}  # chat_id -> agent_id
_chat_listeners: dict[int, asyncio.Task] = {}  # chat_id -> listener task


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Auto-save chat ID for notifications
    chat_id = str(update.effective_chat.id)
    if settings.telegram_chat_id != chat_id:
        settings.telegram_chat_id = chat_id

    await update.message.reply_text(
        "*AI Employee Bot* 🤖\n\n"
        "Commands:\n"
        "/status - Agent Status anzeigen\n"
        "/agents - Alle Agents auflisten\n"
        "/task <text> - Neuen Task erstellen\n"
        "/chat - Chat mit einem Agent starten\n"
        "/stop\\_chat - Chat beenden\n\n"
        "Oder schreib einfach eine Nachricht um mit dem aktiven Agent zu chatten!",
        parse_mode="Markdown",
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{API_BASE}/agents/")
            data = resp.json()

            if not data["agents"]:
                await update.message.reply_text("Keine Agents aktiv. Erstelle einen via Web UI.")
                return

            lines = ["*Agent Status:*\n"]
            for agent in data["agents"]:
                state_emoji = {
                    "running": "🟢",
                    "idle": "🟢",
                    "working": "🔵",
                    "stopped": "🔴",
                    "error": "❌",
                }.get(agent["state"], "⚪")

                task_info = ""
                if agent.get("current_task"):
                    task_info = f"\n  Task: `{agent['current_task']}`"

                lines.append(
                    f"{state_emoji} *{agent['name']}* ({agent['id']})\n"
                    f"  State: {agent['state']}{task_info}"
                )

            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")


async def cmd_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(
            "Usage: /task <beschreibung>\n"
            "Beispiel: /task Erstelle eine Flask API mit User CRUD"
        )
        return

    prompt = " ".join(context.args)

    async with httpx.AsyncClient() as client:
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
                agent_info = f"Agent: `{data.get('agent_id', 'pending')}`"
                await update.message.reply_text(
                    f"✅ *Task erstellt!*\n"
                    f"ID: `{data['id']}`\n"
                    f"Status: {data['status']}\n"
                    f"{agent_info}",
                    parse_mode="Markdown",
                )
            else:
                await update.message.reply_text(f"Error: {data}")
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")


async def cmd_agents(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{API_BASE}/agents/")
            data = resp.json()

            if not data["agents"]:
                await update.message.reply_text(
                    "Keine Agents vorhanden. Erstelle einen via Web UI."
                )
                return

            lines = ["*Agents:*\n"]
            for agent in data["agents"]:
                cpu = f"{agent.get('cpu_percent', 0):.1f}%" if agent.get("cpu_percent") else "N/A"
                mem = f"{agent.get('memory_usage_mb', 0):.0f}MB" if agent.get("memory_usage_mb") else "N/A"
                lines.append(
                    f"*{agent['name']}* (`{agent['id']}`)\n"
                    f"  Model: {agent['model']}\n"
                    f"  CPU: {cpu} | RAM: {mem}\n"
                    f"  Queue: {agent.get('queue_depth', 0)} tasks"
                )

            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")


async def cmd_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start a chat session with an agent."""
    chat_id = update.effective_chat.id

    # If agent specified as argument, use that
    if context.args:
        agent_id = context.args[0]
        await _start_chat_session(update, chat_id, agent_id)
        return

    # Otherwise show agent selection
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{API_BASE}/agents/")
            data = resp.json()

            if not data["agents"]:
                await update.message.reply_text(
                    "Keine Agents vorhanden. Erstelle zuerst einen via Web UI."
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
                    f"{a['name']} ({a['state']})",
                    callback_data=f"chat:{a['id']}"
                )]
                for a in data["agents"]
                if a["state"] in ("running", "idle", "working")
            ]

            if not keyboard:
                await update.message.reply_text("Keine laufenden Agents gefunden.")
                return

            await update.message.reply_text(
                "Waehle einen Agent zum Chatten:",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")


async def cmd_stop_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stop the current chat session."""
    chat_id = update.effective_chat.id

    if chat_id in _active_chats:
        agent_id = _active_chats.pop(chat_id)
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


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle regular text messages - forward to active chat agent."""
    chat_id = update.effective_chat.id

    if chat_id not in _active_chats:
        # No active chat - show hint
        await update.message.reply_text(
            "Kein aktiver Chat. Starte mit /chat einen Chat mit einem Agent."
        )
        return

    agent_id = _active_chats[chat_id]
    text = update.message.text

    # Send to agent via Redis (use internal Docker hostname)
    try:
        redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        message_id = f"tg-{update.message.message_id}"
        payload = json.dumps({
            "id": message_id,
            "text": text,
            "model": None,
        })
        await redis.lpush(f"agent:{agent_id}:chat", payload)
        await redis.aclose()

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
    _active_chats[chat_id] = agent_id

    # Cancel existing listener if any
    if chat_id in _chat_listeners:
        _chat_listeners[chat_id].cancel()

    # Start response listener
    bot = update.get_bot() if callback_query else update.message.get_bot()
    _chat_listeners[chat_id] = asyncio.create_task(
        _listen_agent_responses(bot, chat_id, agent_id)
    )

    msg = (
        f"💬 Chat mit Agent `{agent_id}` gestartet!\n\n"
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
                    tool = event_data.get("tool", "unknown")
                    tool_input = json.dumps(event_data.get("input", {}))[:200]
                    response_buffer += f"\n🔧 [{tool}] {tool_input}\n"

                elif event_type == "error":
                    error_msg = str(event_data.get("message", "Unknown error"))
                    await bot.send_message(
                        chat_id=chat_id,
                        text=f"❌ Error: {error_msg}",
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

                    # Show meta info
                    cost = event_data.get("cost_usd", 0)
                    duration = event_data.get("duration_ms", 0)
                    turns = event_data.get("num_turns", 0)
                    if duration:
                        meta = f"⏱ {duration/1000:.1f}s"
                        if cost:
                            meta += f" | 💰 ${cost:.4f}"
                        if turns:
                            meta += f" | 🔄 {turns} turns"
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
