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

import re as _re

_CODE_BLOCK = _re.compile(r"```.*?```", _re.DOTALL)
_MD_PATTERNS = [
    (_re.compile(r"`([^`]*)`"), r"\1"),                 # inline code
    (_re.compile(r"\*\*([^*]+)\*\*"), r"\1"),           # bold
    (_re.compile(r"\*([^*]+)\*"), r"\1"),               # italic
    (_re.compile(r"^#{1,6}\s*", _re.MULTILINE), ""),    # headers
    (_re.compile(r"\[([^\]]+)\]\([^)]+\)"), r"\1"),     # links → label
    (_re.compile(r"^\s*[-*]\s+", _re.MULTILINE), ""),   # bullet markers
]


def _strip_markdown(text: str) -> str:
    """Flatten Markdown to plain text so TTS doesn't read '*' / backticks aloud."""
    t = _CODE_BLOCK.sub(" (Codeblock) ", text)
    for pattern, repl in _MD_PATTERNS:
        t = pattern.sub(repl, t)
    return t.strip()


class TelegramAgentBot:
    """A Telegram bot instance bound to a single AI Employee agent."""

    def __init__(self, agent_id: str, agent_name: str, bot_token: str, auth_key: str):
        self.agent_id = agent_id
        self.agent_name = agent_name
        self.bot_token = bot_token
        self.auth_key = auth_key
        self.app: Application | None = None
        self._started = False
        self._response_listeners: dict[str, asyncio.Task] = {}
        self._telegram_send_listener: asyncio.Task | None = None

    async def start(self) -> None:
        if self._started:
            return

        self.app = Application.builder().token(self.bot_token).build()

        self.app.add_handler(CommandHandler("start", self._cmd_start))
        self.app.add_handler(CommandHandler("auth", self._cmd_auth))
        self.app.add_handler(CommandHandler("agent", self._cmd_agent))
        self.app.add_handler(CommandHandler("stop", self._cmd_stop))
        self.app.add_handler(CommandHandler("status", self._cmd_status))
        self.app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message)
        )
        self.app.add_handler(
            MessageHandler(
                (filters.PHOTO | filters.Document.ALL | filters.VOICE | filters.AUDIO | filters.VIDEO)
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
        # Listen for agent-initiated `send_telegram` tool calls (proactive pushes)
        self._telegram_send_listener = asyncio.create_task(self._listen_telegram_send())
        print(f"[Telegram] Agent bot started: {self.agent_name} ({self.agent_id})")

    async def stop(self) -> None:
        if not self._started:
            return
        # Cancel all response listeners
        for task in self._response_listeners.values():
            task.cancel()
        self._response_listeners.clear()
        if self._telegram_send_listener:
            self._telegram_send_listener.cancel()
            self._telegram_send_listener = None
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
            for key in [k for k in self._response_listeners if k.startswith(f"{chat_id}:")]:
                self._response_listeners[key].cancel()
                del self._response_listeners[key]
            await update.message.reply_text(
                "Chat beendet. Du wurdest abgemeldet.\n"
                "Nutze /auth <KEY> um dich erneut zu autorisieren."
            )
        else:
            await update.message.reply_text("Du bist nicht autorisiert.")

    async def _cmd_agent(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Switch this Telegram chat to a target agent for future messages."""
        chat_id = update.effective_chat.id
        if not await self._is_authorized(chat_id):
            await update.message.reply_text("Autorisiere dich zuerst mit /auth <KEY>")
            return

        from app.db.session import async_session_factory
        from app.models.agent import Agent
        from sqlalchemy import select

        query = " ".join(context.args).strip()
        async with async_session_factory() as db:
            agents = (await db.execute(select(Agent))).scalars().all()

        if not query:
            lines = ["Ziel-Agent waehlen mit /agent <Name oder ID>:", ""]
            for agent in agents:
                lines.append(f"- {agent.name} ({agent.id})")
            await update.message.reply_text("\n".join(lines))
            return

        query_l = query.lower()
        selected = next(
            (
                agent for agent in agents
                if agent.id.lower().startswith(query_l) or agent.name.lower() == query_l
            ),
            None,
        )
        if not selected:
            selected = next((agent for agent in agents if query_l in agent.name.lower()), None)
        if not selected:
            await update.message.reply_text(f"Keinen Agent gefunden fuer: {query}")
            return

        redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            await redis.setex(f"telegram:chat:{chat_id}:active_agent", 86400, selected.id)
        finally:
            await redis.aclose()
        self._start_listener(chat_id, selected.id)
        await update.message.reply_text(
            f"Telegram-Ziel gesetzt: {selected.name} ({selected.id}).\n"
            "Deine naechsten Nachrichten gehen an diesen Agent."
        )

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        if not await self._is_authorized(chat_id):
            await update.message.reply_text(
                "Autorisiere dich zuerst mit /auth <KEY>"
            )
            return

        from app.telegram._bridge_auth import authed_client
        try:
            async with await authed_client() as client:
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

        target_agent_id = await self._active_target_agent_id(chat_id)
        print(
            f"[Telegram] inbound text chat={chat_id} gateway={self.agent_id} "
            f"target={target_agent_id} message={update.message.message_id}"
        )

        # Ensure response listener is running for the target agent
        self._start_listener(chat_id, target_agent_id)

        # Build Telegram context so the agent knows the source and can reply directly
        tg_context = {
            "source": "telegram",
            "gateway_agent_id": self.agent_id,
            "target_agent_id": target_agent_id,
            "chat_id": chat_id,
            "message_id": update.message.message_id,
            "user_id": user.id if user else None,
            "username": user.username if user else None,
            "first_name": user.first_name if user else None,
            "chat_type": update.effective_chat.type,
        }

        # Wake up agent if stopped (auto-lifecycle), with user-visible status messages
        woke_up = await self._ensure_agent_running(update, target_agent_id)

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
            await redis.lpush(f"agent:{target_agent_id}:chat", payload)
            await redis.aclose()

            await update.effective_chat.send_action("typing")
            if woke_up:
                await update.message.reply_text("✅ Agent hochgefahren!")
        except Exception as e:
            await update.message.reply_text(f"Fehler beim Senden: {e}")

    async def _active_target_agent_id(self, chat_id: int) -> str:
        """Return the agent Telegram replies should currently go to.

        When another agent uses this bot as a fallback outbound channel, the
        fallback sender stores a short-lived chat->agent route. Replies then go
        back to that agent instead of the bot-owning gateway agent.
        """
        redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            target = await redis.get(f"telegram:chat:{chat_id}:active_agent")
            return target or self.agent_id
        finally:
            await redis.aclose()

    async def _ensure_agent_running(self, update: Update, target_agent_id: str | None = None) -> bool:
        """If this agent's container is stopped, wake it up. Returns True if we had to wake.

        Sends user-visible messages: "Agent fährt hoch, einen Moment!" then "Agent hochgefahren!"
        Always verifies actual Docker container state — DB may be stale after a restart.
        """
        try:
            from app.db.session import async_session_factory
            from app.models.agent import Agent, AgentState
            from app.services.user_lifecycle import wake_agent
            from app.services.docker_service import DockerService
            from sqlalchemy import select

            docker = DockerService()

            async with async_session_factory() as db:
                agent = await db.scalar(select(Agent).where(Agent.id == (target_agent_id or self.agent_id)))
                if not agent:
                    return False

                # Check actual container state — DB may be stale after orchestrator restart
                needs_wake = False
                if agent.state not in (AgentState.RUNNING, AgentState.IDLE, AgentState.WORKING):
                    needs_wake = True
                elif agent.container_id:
                    container_status = docker.get_container_status(agent.container_id)
                    if container_status not in ("running", "created"):
                        # DB says running but container is actually stopped — fix DB state too
                        agent.state = AgentState.STOPPED
                        await db.commit()
                        needs_wake = True

                if not needs_wake:
                    return False

                # Needs waking — tell user and start
                await update.message.reply_text("⏳ Agent fährt hoch, einen Moment...")
                ok = await wake_agent(db, docker, target_agent_id or self.agent_id, wait=True, timeout=20)
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
        target_agent_id = await self._active_target_agent_id(chat_id)
        print(
            f"[Telegram] inbound media chat={chat_id} gateway={self.agent_id} "
            f"target={target_agent_id} message={update.message.message_id}"
        )
        self._start_listener(chat_id, target_agent_id)

        # Determine media type and file_id
        media_type = "unknown"
        file_id = None
        caption = update.message.caption or ""
        # An image to hand straight to the (multimodal) agent, if any.
        image_file_id: str | None = None
        image_mime: str = "image/jpeg"

        if update.message.photo:
            media_type = "photo"
            file_id = update.message.photo[-1].file_id  # Highest resolution
            image_file_id = file_id
        elif update.message.document:
            media_type = "document"
            file_id = update.message.document.file_id
            caption = caption or update.message.document.file_name or ""
            doc_mime = (update.message.document.mime_type or "").lower()
            if doc_mime in ("image/jpeg", "image/png", "image/gif", "image/webp"):
                image_file_id = file_id
                image_mime = doc_mime
        elif update.message.voice:
            media_type = "voice"
            file_id = update.message.voice.file_id
        elif update.message.audio:
            media_type = "audio"
            file_id = update.message.audio.file_id
        elif update.message.video:
            media_type = "video"
            file_id = update.message.video.file_id

        tg_context = {
            "source": "telegram",
            "gateway_agent_id": self.agent_id,
            "target_agent_id": target_agent_id,
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

        # Photos (and image documents) are downloaded here and handed to the
        # agent directly as a vision image — no tool call, no token needed.
        images: list[dict] = []
        if image_file_id:
            try:
                import base64 as _b64
                tg_file = await context.bot.get_file(image_file_id)
                raw = bytes(await tg_file.download_as_bytearray())
                if 0 < len(raw) <= 5 * 1024 * 1024:
                    images.append({
                        "media_type": image_mime,
                        "data": _b64.b64encode(raw).decode("ascii"),
                    })
                    if not caption:
                        text = "[Telegram photo] (see attached image)"
            except Exception as e:
                logging.getLogger(__name__).warning("Telegram image download failed: %s", e)

        # Voice/audio messages are transcribed here (local faster-whisper) so
        # the agent receives plain text — never raw audio it can't process.
        if media_type in ("voice", "audio") and file_id:
            try:
                import httpx
                tg_file = await context.bot.get_file(file_id)
                raw = bytes(await tg_file.download_as_bytearray())
                async with httpx.AsyncClient(timeout=120) as client:
                    resp = await client.post(
                        f"{settings.stt_service_url}/transcribe",
                        files={"file": ("voice.ogg", raw, "audio/ogg")},
                    )
                resp.raise_for_status()
                transcript = (resp.json().get("text") or "").strip()
                if transcript:
                    prefix = f"{caption}\n\n" if caption else ""
                    text = f"{prefix}[Telegram-Sprachnachricht, transkribiert:]\n{transcript}"
                else:
                    text = "[Telegram-Sprachnachricht — leer oder unverständlich]"
            except Exception as e:
                logging.getLogger(__name__).warning("Voice transcription failed: %s", e)
                text = (
                    f"[Telegram {media_type}] Transkription fehlgeschlagen "
                    f"(file_id: {file_id}). Du kannst die Datei per get-file laden."
                )

        try:
            redis = aioredis.from_url(settings.redis_url, decode_responses=True)
            message_id = f"tg-{update.message.message_id}"
            payload = json.dumps({
                "id": message_id,
                "text": text,
                "model": None,
                "telegram": tg_context,
                "images": images,
            })
            await redis.lpush(f"agent:{target_agent_id}:chat", payload)
            # Voice-first: a voice/audio message should get a voice reply.
            if media_type in ("voice", "audio"):
                await redis.setex(f"agent:{target_agent_id}:voicereply:{message_id}", 3600, "1")
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
                        approval_id = meta.get("approval_id")
                        if approval_id:
                            from app.models.command_approval import ApprovalStatus, CommandApproval
                            approval = await db.scalar(
                                select(CommandApproval).where(CommandApproval.id == int(approval_id))
                            )
                            if approval and approval.status == ApprovalStatus.PENDING:
                                negative = choice.lower() in {
                                    "deny", "denied", "no", "nein", "cancel", "abort", "ablehnen"
                                }
                                approval.status = ApprovalStatus.DENIED if negative else ApprovalStatus.APPROVED
                                approval.resolved_at = datetime.now(timezone.utc)
                                approval.user_response = choice
                        await db.commit()
                redis = aioredis.from_url(settings.redis_url, decode_responses=True)
                await redis.set(f"approval:result:{notif_id}", choice, ex=3600)
                if "approval_id" in locals() and approval_id:
                    await redis.publish(f"approval:{approval_id}", json.dumps({
                        "status": "denied" if choice.lower() in {
                            "deny", "denied", "no", "nein", "cancel", "abort", "ablehnen"
                        } else "approved",
                        "approval_id": str(approval_id),
                        "reason": choice,
                    }))
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

    def _start_listener(self, chat_id: int, target_agent_id: str | None = None) -> None:
        listen_agent_id = target_agent_id or self.agent_id
        key = f"{chat_id}:{listen_agent_id}"
        if key in self._response_listeners:
            if not self._response_listeners[key].done():
                return
        self._response_listeners[key] = asyncio.create_task(
            self._listen_responses(chat_id, listen_agent_id)
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

    async def _listen_telegram_send(self) -> None:
        """Forward agent-initiated `send_telegram` tool calls to Telegram users.

        The agent publishes to `agent:{id}:telegram:send`; this delivers the
        message (and optional base64 file) to every authorized chat.
        """
        redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        pubsub = redis.pubsub()
        try:
            await pubsub.subscribe(f"agent:{self.agent_id}:telegram:send")
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    await self._deliver_telegram_send(json.loads(message["data"]))
                except Exception as e:
                    print(f"[Telegram] send_telegram delivery failed: {e}")
        except asyncio.CancelledError:
            return
        except Exception as e:
            print(f"[Telegram] telegram:send listener error: {e}")
        finally:
            try:
                await pubsub.aclose()
                await redis.aclose()
            except Exception:
                pass

    async def _deliver_telegram_send(self, data: dict) -> None:
        """Send one agent-pushed message/file to all authorized chats."""
        import base64
        import io

        if not self.app or not self._started:
            return
        text = (data.get("text") or "").strip()
        file_b64 = data.get("file_b64")
        media_type = data.get("media_type") or ""
        filename = data.get("filename") or "file"
        caption = (data.get("caption") or text or "")[:1024]

        redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            chat_ids = await redis.smembers(f"agent:{self.agent_id}:tg_auth")
        finally:
            await redis.aclose()

        for cid in chat_ids:
            try:
                cid_int = int(cid)
                if file_b64:
                    raw = base64.b64decode(file_b64)
                    bio = io.BytesIO(raw)
                    bio.name = filename
                    if media_type.startswith("image/"):
                        await self.app.bot.send_photo(
                            chat_id=cid_int, photo=bio, caption=caption or None
                        )
                    else:
                        await self.app.bot.send_document(
                            chat_id=cid_int, document=bio, caption=caption or None
                        )
                elif text:
                    await self._send_chunked(cid_int, text)
            except Exception as e:
                print(f"[Telegram] send_telegram → chat {cid} failed: {e}")

    async def _listen_responses(self, chat_id: int, listen_agent_id: str | None = None) -> None:
        """Listen to agent chat responses and forward to Telegram with streaming.

        Only forwards responses to Telegram-originated messages (tg- prefix).
        Tool calls are hidden — a typing indicator is shown instead.
        Text is streamed with periodic flush for a responsive feel.
        """
        try:
            redis = aioredis.from_url(settings.redis_url, decode_responses=True)
            pubsub = redis.pubsub()
            agent_id = listen_agent_id or self.agent_id
            await pubsub.subscribe(f"agent:{agent_id}:chat:response")

            response_buffer = ""
            full_response = ""  # whole turn's text — for the voice-first reply
            last_flush = asyncio.get_event_loop().time()

            # Detect agent mode: custom_llm streams slower → flush per sentence
            _is_local_llm = False
            try:
                from app.db.session import async_session_factory
                from app.models.agent import Agent
                from sqlalchemy import select
                async with async_session_factory() as _db:
                    _agent = (await _db.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none()
                    _is_local_llm = _agent and _agent.mode == "custom_llm"
            except Exception:
                pass

            # Local LLMs: collect everything, send once at the end (cleaner output)
            # Cloud LLMs: stream with periodic flush (faster perceived response)
            FLUSH_INTERVAL = 999.0 if _is_local_llm else 3.0
            MIN_CHUNK_SIZE = 99999 if _is_local_llm else 100
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
                        _chunk = str(event_data.get("text", ""))
                        response_buffer += _chunk
                        full_response += _chunk

                    elif event_type == "tool_call":
                        # Send whatever the agent wrote before the tool call
                        # (e.g. "Moment, ich schaue nach..." — the agent decides the wording)
                        if response_buffer.strip():
                            try:
                                await self._send_chunked(chat_id, response_buffer.strip())
                            except Exception:
                                pass
                            response_buffer = ""
                            last_flush = now
                        # Show typing indicator while tool runs
                        try:
                            await self.app.bot.send_chat_action(chat_id=chat_id, action="typing")
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
                        # For local LLMs: small delay to catch any trailing text events
                        if _is_local_llm:
                            await asyncio.sleep(0.5)
                            # Drain any remaining text events from pubsub
                            for _ in range(50):
                                trailing = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.05)
                                if not trailing or trailing["type"] != "message":
                                    break
                                try:
                                    td = json.loads(trailing["data"])
                                    if td.get("type") == "text":
                                        _t = str(td.get("data", {}).get("text", ""))
                                        response_buffer += _t
                                        full_response += _t
                                except Exception:
                                    pass
                        # Flush remaining buffer
                        if response_buffer.strip():
                            await self._send_chunked(chat_id, response_buffer.strip())
                        response_buffer = ""
                        last_flush = now

                        # Voice-first: if the user spoke, reply with voice too.
                        await self._maybe_send_voice_reply(chat_id, msg_id, full_response)
                        full_response = ""

                        duration = event_data.get("duration_ms", 0)
                        turns = event_data.get("num_turns", 0)
                        if duration:
                            meta = f"⏱ {duration / 1000:.1f}s | 🔄 {turns} turns"
                            await self.app.bot.send_message(
                                chat_id=chat_id, text=meta
                            )

                # Periodic flush
                if response_buffer.strip() and (now - last_flush) >= FLUSH_INTERVAL:
                    # For local LLMs: flush at sentence boundaries for cleaner output
                    if _is_local_llm:
                        import re
                        # Check if buffer ends with a sentence (. ! ? followed by space/newline/end)
                        if re.search(r'[.!?]\s*$', response_buffer) or len(response_buffer.strip()) >= 500:
                            await self._send_chunked(chat_id, response_buffer.strip())
                            response_buffer = ""
                            last_flush = now
                    elif len(response_buffer.strip()) >= MIN_CHUNK_SIZE:
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

    async def _maybe_send_voice_reply(self, chat_id: int, msg_id: str, text: str) -> None:
        """Voice-first: if this message arrived as a voice/audio message, also
        send the agent's reply back as a Telegram voice message (TTS).

        The text reply is sent regardless (above) — voice is added on top so
        the user keeps links/code while getting the spoken, colleague-like feel.
        Best-effort: a failing TTS service never breaks the text response.
        """
        if not msg_id or not text.strip():
            return

        redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            key = f"agent:{self.agent_id}:voicereply:{msg_id}"
            if not await redis.get(key):
                return
            await redis.delete(key)
        finally:
            await redis.aclose()

        spoken = _strip_markdown(text)[:4000]
        if not spoken.strip():
            return

        try:
            import io
            import httpx
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{settings.tts_service_url}/synthesize",
                    json={"text": spoken, "language": "de"},
                )
                resp.raise_for_status()
                audio = resp.content
            if not audio:
                return
            await self.app.bot.send_chat_action(chat_id=chat_id, action="record_voice")
            await self.app.bot.send_voice(chat_id=chat_id, voice=io.BytesIO(audio))
        except Exception as e:
            logging.getLogger(__name__).warning("Voice reply (TTS) failed: %s", e)
