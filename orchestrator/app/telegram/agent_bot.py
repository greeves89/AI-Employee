"""Per-agent Telegram bot with auth-key based access control.

Each agent can have its own Telegram bot (via BotFather token).
Users must authenticate with /auth <key> before chatting.
"""

import asyncio
import json
import uuid

import redis.asyncio as aioredis
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from app.config import settings


class TelegramAgentBot:
    """A Telegram bot instance bound to a single AI Employee agent."""

    def __init__(self, agent_id: str, agent_name: str, bot_token: str, auth_key: str):
        self.agent_id = agent_id
        self.agent_name = agent_name
        self.bot_token = bot_token
        self.auth_key = auth_key
        self.app: Application | None = None
        self._started = False
        self._response_listeners: dict[int, asyncio.Task] = {}

    async def start(self) -> None:
        if self._started:
            return

        self.app = Application.builder().token(self.bot_token).build()

        self.app.add_handler(CommandHandler("start", self._cmd_start))
        self.app.add_handler(CommandHandler("auth", self._cmd_auth))
        self.app.add_handler(CommandHandler("stop", self._cmd_stop))
        self.app.add_handler(CommandHandler("status", self._cmd_status))
        self.app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message)
        )

        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=["message"],
        )
        self._started = True
        print(f"[Telegram] Agent bot started: {self.agent_name} ({self.agent_id})")

    async def stop(self) -> None:
        if not self._started:
            return
        # Cancel all response listeners
        for task in self._response_listeners.values():
            task.cancel()
        self._response_listeners.clear()
        try:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
        except Exception:
            pass
        self._started = False
        print(f"[Telegram] Agent bot stopped: {self.agent_name} ({self.agent_id})")

    # --- Auth helpers ---

    async def _is_authorized(self, chat_id: int) -> bool:
        redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            return await redis.sismember(f"agent:{self.agent_id}:tg_auth", str(chat_id))
        finally:
            await redis.aclose()

    async def _authorize(self, chat_id: int) -> None:
        redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            await redis.sadd(f"agent:{self.agent_id}:tg_auth", str(chat_id))
        finally:
            await redis.aclose()

    async def _deauthorize(self, chat_id: int) -> None:
        redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            await redis.srem(f"agent:{self.agent_id}:tg_auth", str(chat_id))
        finally:
            await redis.aclose()

    # --- Command handlers ---

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        if await self._is_authorized(chat_id):
            await update.message.reply_text(
                f"*{self.agent_name}*\n\n"
                f"Du bist autorisiert. Schreib einfach eine Nachricht!\n\n"
                f"/status - Agent Status\n"
                f"/stop - Chat beenden & abmelden",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(
                f"*{self.agent_name}*\n\n"
                f"Bitte autorisiere dich mit deinem Key:\n"
                f"/auth <DEIN\\_KEY>\n\n"
                f"Den Key findest du in den Agent-Einstellungen der Web-Oberflaeche.",
                parse_mode="Markdown",
            )

    async def _cmd_auth(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id

        if not context.args:
            await update.message.reply_text(
                "Bitte gib deinen Key an: /auth <KEY>"
            )
            return

        provided_key = context.args[0].strip()
        if provided_key == self.auth_key:
            await self._authorize(chat_id)
            # Start response listener
            self._start_listener(chat_id)
            await update.message.reply_text(
                f"Autorisierung erfolgreich!\n\n"
                f"Du kannst jetzt mit *{self.agent_name}* chatten.\n"
                f"Schreib einfach eine Nachricht.",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(
                "Falscher Key. Bitte versuche es erneut."
            )

    async def _cmd_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        if await self._is_authorized(chat_id):
            await self._deauthorize(chat_id)
            if chat_id in self._response_listeners:
                self._response_listeners[chat_id].cancel()
                del self._response_listeners[chat_id]
            await update.message.reply_text(
                "Chat beendet. Du wurdest abgemeldet.\n"
                "Nutze /auth <KEY> um dich erneut zu autorisieren."
            )
        else:
            await update.message.reply_text("Du bist nicht autorisiert.")

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        if not await self._is_authorized(chat_id):
            await update.message.reply_text(
                "Autorisiere dich zuerst mit /auth <KEY>"
            )
            return

        import httpx
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"http://127.0.0.1:8000/api/v1/agents/{self.agent_id}")
                data = resp.json()
                state = data.get("state", "unknown")
                state_emoji = {
                    "running": "🟢", "idle": "🟢", "working": "🔵",
                    "stopped": "🔴", "error": "❌",
                }.get(state, "⚪")
                cpu = data.get("cpu_percent", 0)
                mem = data.get("memory_usage_mb", 0)
                await update.message.reply_text(
                    f"{state_emoji} *{self.agent_name}*\n"
                    f"Status: {state}\n"
                    f"CPU: {cpu:.1f}% | RAM: {mem:.0f}MB",
                    parse_mode="Markdown",
                )
        except Exception as e:
            await update.message.reply_text(f"Fehler: {e}")

    # --- Message handling ---

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id

        if not await self._is_authorized(chat_id):
            await update.message.reply_text(
                "Autorisiere dich zuerst mit /auth <KEY>"
            )
            return

        text = update.message.text

        # Ensure response listener is running
        self._start_listener(chat_id)

        # Send message to agent via Redis
        try:
            redis = aioredis.from_url(settings.redis_url, decode_responses=True)
            message_id = f"tg-{update.message.message_id}"
            payload = json.dumps({
                "id": message_id,
                "text": text,
                "model": None,
            })
            await redis.lpush(f"agent:{self.agent_id}:chat", payload)
            await redis.aclose()

            await update.effective_chat.send_action("typing")
        except Exception as e:
            await update.message.reply_text(f"Fehler beim Senden: {e}")

    # --- Response listener ---

    def _start_listener(self, chat_id: int) -> None:
        if chat_id in self._response_listeners:
            if not self._response_listeners[chat_id].done():
                return
        self._response_listeners[chat_id] = asyncio.create_task(
            self._listen_responses(chat_id)
        )

    async def _listen_responses(self, chat_id: int) -> None:
        """Listen to agent chat responses and forward to Telegram."""
        try:
            redis = aioredis.from_url(settings.redis_url, decode_responses=True)
            pubsub = redis.pubsub()
            await pubsub.subscribe(f"agent:{self.agent_id}:chat:response")

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
                        await self.app.bot.send_message(
                            chat_id=chat_id, text=f"❌ Fehler: {error_msg}"
                        )
                        response_buffer = ""

                    elif event_type == "done":
                        if response_buffer.strip():
                            text = response_buffer.strip()
                            for i in range(0, len(text), 4000):
                                chunk = text[i : i + 4000]
                                await self.app.bot.send_message(
                                    chat_id=chat_id, text=chunk
                                )
                        response_buffer = ""

                        cost = event_data.get("cost_usd", 0)
                        duration = event_data.get("duration_ms", 0)
                        turns = event_data.get("num_turns", 0)
                        if duration:
                            meta = f"⏱ {duration / 1000:.1f}s"
                            if cost:
                                meta += f" | 💰 ${cost:.4f}"
                            if turns:
                                meta += f" | 🔄 {turns} turns"
                            await self.app.bot.send_message(
                                chat_id=chat_id, text=meta
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
