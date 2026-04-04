import asyncio
import json
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters

from app.config import settings
from app.telegram.handlers.commands import (
    cmd_start,
    cmd_status,
    cmd_task,
    cmd_agents,
    cmd_chat,
    cmd_stop_chat,
    handle_callback,
    handle_message,
)
from app.telegram.handlers.voice import handle_voice

logger = logging.getLogger(__name__)


class TelegramBot:
    """Telegram bot for controlling AI Employee agents remotely."""

    def __init__(self):
        self.app: Application | None = None
        self._started = False
        self._rating_listener: asyncio.Task | None = None

    async def start(self) -> None:
        if not settings.telegram_bot_token or self._started:
            return

        self._started = True

        self.app = (
            Application.builder()
            .token(settings.telegram_bot_token)
            .build()
        )

        # Register command handlers
        self.app.add_handler(CommandHandler("start", cmd_start))
        self.app.add_handler(CommandHandler("status", cmd_status))
        self.app.add_handler(CommandHandler("task", cmd_task))
        self.app.add_handler(CommandHandler("agents", cmd_agents))
        self.app.add_handler(CommandHandler("chat", cmd_chat))
        self.app.add_handler(CommandHandler("stop_chat", cmd_stop_chat))

        # Callback handler for inline keyboards
        self.app.add_handler(CallbackQueryHandler(handle_callback))

        # Voice message handler
        self.app.add_handler(MessageHandler(
            filters.VOICE | filters.AUDIO,
            handle_voice,
        ))

        # Message handler for chat (must be last!)
        self.app.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message,
        ))

        await self.app.initialize()
        await self.app.start()

        # Start polling - drop pending to avoid processing stale messages
        await self.app.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query", "voice"],
        )
        print("[Telegram] Bot started: @DaAIEmployeeBot")

        # Start listening for rating request messages from task_router
        self._rating_listener = asyncio.create_task(self._listen_rating_requests())

    async def stop(self) -> None:
        if self._rating_listener:
            self._rating_listener.cancel()
            self._rating_listener = None
        if self.app and self._started:
            try:
                await self.app.updater.stop()
                await self.app.stop()
                await self.app.shutdown()
            except Exception:
                pass
            self._started = False

    async def send_notification(self, message: str) -> None:
        if self.app and settings.telegram_chat_id:
            await self.app.bot.send_message(
                chat_id=settings.telegram_chat_id,
                text=message,
                parse_mode="Markdown",
            )

    async def _listen_rating_requests(self) -> None:
        """Listen for rating request messages from task_router via Redis PubSub."""
        import redis.asyncio as aioredis

        try:
            redis_client = aioredis.from_url(settings.redis_url)
            pubsub = redis_client.pubsub()
            await pubsub.subscribe("telegram:rating_request")
            logger.info("[Telegram] Listening for rating requests on Redis")

            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    data = json.loads(message["data"])
                    chat_id = settings.telegram_chat_id
                    if not chat_id or not self.app:
                        continue

                    # Build inline keyboard from data
                    markup_data = data.get("reply_markup", {})
                    keyboard = []
                    for row in markup_data.get("inline_keyboard", []):
                        keyboard.append([
                            InlineKeyboardButton(
                                text=btn["text"],
                                callback_data=btn["callback_data"],
                            )
                            for btn in row
                        ])

                    await self.app.bot.send_message(
                        chat_id=int(chat_id),
                        text=data.get("text", "Bitte bewerten:"),
                        reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None,
                    )
                except Exception as e:
                    logger.warning(f"[Telegram] Failed to send rating request: {e}")
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error(f"[Telegram] Rating listener error: {e}")
