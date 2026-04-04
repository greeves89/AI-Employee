"""Per-agent Telegram bot with auth-key based access control.

Each agent can have its own Telegram bot (via BotFather token).
Users must authenticate with /auth <key> before chatting.
"""

import asyncio
import json
import logging
import uuid

import redis.asyncio as aioredis
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
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
        self.app.add_handler(
            MessageHandler(
                (filters.PHOTO | filters.Document.ALL | filters.VOICE | filters.VIDEO)
                & ~filters.COMMAND,
                self._handle_media,
            )
        )
        self.app.add_handler(CallbackQueryHandler(self._handle_callback))

        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query"],
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
        user = update.effective_user

        # Ensure response listener is running
        self._start_listener(chat_id)

        # Build Telegram context so the agent knows the source and can reply directly
        tg_context = {
            "source": "telegram",
            "chat_id": chat_id,
            "message_id": update.message.message_id,
            "user_id": user.id if user else None,
            "username": user.username if user else None,
            "first_name": user.first_name if user else None,
            "chat_type": update.effective_chat.type,
        }

        # Wake up agent if stopped (auto-lifecycle), with user-visible status messages
        woke_up = await self._ensure_agent_running(update)

        # Send message to agent via Redis
        try:
            redis = aioredis.from_url(settings.redis_url, decode_responses=True)
            message_id = f"tg-{update.message.message_id}"
            payload = json.dumps({
                "id": message_id,
                "text": text,
                "model": None,
                "telegram": tg_context,
            })
            await redis.lpush(f"agent:{self.agent_id}:chat", payload)
            await redis.aclose()

            await update.effective_chat.send_action("typing")
            if woke_up:
                await update.message.reply_text("✅ Agent hochgefahren!")
        except Exception as e:
            await update.message.reply_text(f"Fehler beim Senden: {e}")

    async def _ensure_agent_running(self, update: Update) -> bool:
        """If this agent's container is stopped, wake it up. Returns True if we had to wake.

        Sends user-visible messages: "Agent fährt hoch, einen Moment!" then "Agent hochgefahren!"
        """
        try:
            from app.db.session import async_session_factory
            from app.models.agent import Agent, AgentState
            from app.services.user_lifecycle import wake_agent
            from sqlalchemy import select

            async with async_session_factory() as db:
                agent = await db.scalar(select(Agent).where(Agent.id == self.agent_id))
                if not agent:
                    return False
                if agent.state in (AgentState.RUNNING, AgentState.IDLE, AgentState.WORKING):
                    return False

                # Needs waking — tell user and start
                await update.message.reply_text("⏳ Agent fährt hoch, einen Moment...")
                from app.services.docker_service import DockerService
                docker = DockerService()
                ok = await wake_agent(db, docker, self.agent_id, wait=True, timeout=20)
                return ok
        except Exception as e:
            logging.getLogger(__name__).warning(f"Telegram wake-up failed: {e}")
            return False

    # --- Media & callback handling ---

    async def _handle_media(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Forward photos, documents, voice, video to the agent with metadata."""
        chat_id = update.effective_chat.id
        if not await self._is_authorized(chat_id):
            await update.message.reply_text("Autorisiere dich zuerst mit /auth <KEY>")
            return

        user = update.effective_user
        self._start_listener(chat_id)

        # Determine media type and file_id
        media_type = "unknown"
        file_id = None
        caption = update.message.caption or ""

        if update.message.photo:
            media_type = "photo"
            file_id = update.message.photo[-1].file_id  # Highest resolution
        elif update.message.document:
            media_type = "document"
            file_id = update.message.document.file_id
            caption = caption or update.message.document.file_name or ""
        elif update.message.voice:
            media_type = "voice"
            file_id = update.message.voice.file_id
        elif update.message.video:
            media_type = "video"
            file_id = update.message.video.file_id

        tg_context = {
            "source": "telegram",
            "chat_id": chat_id,
            "message_id": update.message.message_id,
            "user_id": user.id if user else None,
            "username": user.username if user else None,
            "first_name": user.first_name if user else None,
            "chat_type": update.effective_chat.type,
            "media_type": media_type,
            "file_id": file_id,
        }

        text = f"[Telegram {media_type}] {caption}".strip() if caption else f"[Telegram {media_type} received, file_id: {file_id}]"

        try:
            redis = aioredis.from_url(settings.redis_url, decode_responses=True)
            message_id = f"tg-{update.message.message_id}"
            payload = json.dumps({
                "id": message_id,
                "text": text,
                "model": None,
                "telegram": tg_context,
            })
            await redis.lpush(f"agent:{self.agent_id}:chat", payload)
            await redis.aclose()
            await update.effective_chat.send_action("typing")
        except Exception as e:
            await update.message.reply_text(f"Fehler beim Senden: {e}")

    async def _handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Forward inline keyboard button presses to the agent."""
        query = update.callback_query
        if not query:
            return

        chat_id = query.message.chat.id if query.message else 0
        if not await self._is_authorized(chat_id):
            await query.answer("Nicht autorisiert")
            return

        # Special handling for approval responses (format: "approval:{notif_id}:{choice}")
        if query.data and query.data.startswith("approval:"):
            try:
                parts = query.data.split(":", 2)
                notif_id = int(parts[1])
                choice = parts[2]
                # Write directly to DB + Redis (no chat loop needed)
                from app.db.session import async_session_factory
                from app.models.notification import Notification
                from sqlalchemy import select
                from datetime import datetime, timezone
                async with async_session_factory() as db:
                    notif = await db.scalar(select(Notification).where(Notification.id == notif_id))
                    if notif:
                        meta = dict(notif.meta or {})
                        meta["response"] = choice
                        meta["responded_at"] = datetime.now(timezone.utc).isoformat()
                        meta["responded_via"] = "telegram"
                        notif.meta = meta
                        notif.read = True
                        await db.commit()
                redis = aioredis.from_url(settings.redis_url, decode_responses=True)
                await redis.set(f"approval:result:{notif_id}", choice, ex=3600)
                await redis.aclose()
                await query.answer(f"✓ {choice}")
                await query.edit_message_reply_markup(reply_markup=None)
                await query.message.reply_text(f"✓ Deine Wahl: *{choice}*", parse_mode="Markdown")
            except Exception as e:
                await query.answer(f"Fehler: {e}")
            return

        user = query.from_user
        self._start_listener(chat_id)

        tg_context = {
            "source": "telegram",
            "chat_id": chat_id,
            "message_id": query.message.message_id if query.message else None,
            "user_id": user.id if user else None,
            "username": user.username if user else None,
            "first_name": user.first_name if user else None,
            "chat_type": query.message.chat.type if query.message else "private",
            "callback_query_id": query.id,
            "callback_data": query.data,
        }

        text = f"[Callback] {query.data}"

        try:
            redis = aioredis.from_url(settings.redis_url, decode_responses=True)
            message_id = f"tg-cb-{query.id}"
            payload = json.dumps({
                "id": message_id,
                "text": text,
                "model": None,
                "telegram": tg_context,
            })
            await redis.lpush(f"agent:{self.agent_id}:chat", payload)
            await redis.aclose()
            # Acknowledge the callback (prevents loading spinner)
            await query.answer()
        except Exception as e:
            await query.answer(f"Fehler: {e}")

    # --- Response listener ---

    def _start_listener(self, chat_id: int) -> None:
        if chat_id in self._response_listeners:
            if not self._response_listeners[chat_id].done():
                return
        self._response_listeners[chat_id] = asyncio.create_task(
            self._listen_responses(chat_id)
        )

    async def send_to_all_authorized(self, text: str, reply_markup=None) -> None:
        """Send a message to ALL authorized Telegram users of this agent.

        If reply_markup is provided, the message is sent with inline keyboard (not chunked).
        """
        if not self.app or not self._started:
            return
        redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            chat_ids = await redis.smembers(f"agent:{self.agent_id}:tg_auth")
            for cid in chat_ids:
                try:
                    if reply_markup is not None:
                        await self.app.bot.send_message(
                            chat_id=int(cid),
                            text=text,
                            reply_markup=reply_markup,
                            parse_mode="Markdown",
                        )
                    else:
                        await self._send_chunked(int(cid), text)
                except Exception:
                    pass
        finally:
            await redis.aclose()

    async def _listen_responses(self, chat_id: int) -> None:
        """Listen to agent chat responses and forward to Telegram with streaming.

        Only forwards responses to Telegram-originated messages (tg- prefix).
        Tool calls are hidden — a typing indicator is shown instead.
        Text is streamed with periodic flush for a responsive feel.
        """
        try:
            redis = aioredis.from_url(settings.redis_url, decode_responses=True)
            pubsub = redis.pubsub()
            await pubsub.subscribe(f"agent:{self.agent_id}:chat:response")

            response_buffer = ""
            last_flush = asyncio.get_event_loop().time()
            FLUSH_INTERVAL = 3.0  # Send buffered text every 3 seconds
            MIN_CHUNK_SIZE = 100  # Don't send tiny fragments
            _typing_sent = False

            while True:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=0.5
                )
                now = asyncio.get_event_loop().time()

                if message and message["type"] == "message":
                    data = json.loads(message["data"])
                    event_type = data.get("type", "")
                    event_data = data.get("data", {})
                    msg_id = data.get("message_id", "")

                    # Only forward responses to Telegram-originated messages
                    if msg_id and not msg_id.startswith("tg-"):
                        continue

                    if event_type == "text":
                        response_buffer += str(event_data.get("text", ""))

                    elif event_type == "tool_call":
                        # Flush buffered text before tool runs so user sees
                        # intermediate messages like "Lass mich nachschauen..."
                        if response_buffer.strip():
                            await self._send_chunked(chat_id, response_buffer.strip())
                            response_buffer = ""
                            last_flush = now
                        # Show typing indicator while tools are running
                        if not _typing_sent:
                            try:
                                await self.app.bot.send_chat_action(
                                    chat_id=chat_id, action="typing"
                                )
                                _typing_sent = True
                            except Exception:
                                pass

                    elif event_type == "error":
                        error_msg = str(event_data.get("message", "Unknown error"))
                        await self.app.bot.send_message(
                            chat_id=chat_id, text=f"❌ {error_msg}"
                        )
                        response_buffer = ""

                    elif event_type == "done":
                        _typing_sent = False
                        # Flush remaining buffer
                        if response_buffer.strip():
                            await self._send_chunked(chat_id, response_buffer.strip())
                        response_buffer = ""
                        last_flush = now

                        duration = event_data.get("duration_ms", 0)
                        turns = event_data.get("num_turns", 0)
                        if duration:
                            meta = f"⏱ {duration / 1000:.1f}s | 🔄 {turns} turns"
                            await self.app.bot.send_message(
                                chat_id=chat_id, text=meta
                            )

                # Periodic flush: send buffered text every FLUSH_INTERVAL seconds
                if response_buffer.strip() and (now - last_flush) >= FLUSH_INTERVAL:
                    if len(response_buffer.strip()) >= MIN_CHUNK_SIZE:
                        await self._send_chunked(chat_id, response_buffer.strip())
                        response_buffer = ""
                        last_flush = now

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

    async def _send_chunked(self, chat_id: int, text: str) -> None:
        """Send text in Telegram-safe chunks (max 4096 chars)."""
        for i in range(0, len(text), 4000):
            chunk = text[i : i + 4000]
            await self.app.bot.send_message(chat_id=chat_id, text=chunk)
