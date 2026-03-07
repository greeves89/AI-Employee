import asyncio

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


class TelegramBot:
    """Telegram bot for controlling AI Employee agents remotely."""

    def __init__(self):
        self.app: Application | None = None
        self._started = False

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

    async def stop(self) -> None:
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
