"""Manager for per-agent Telegram bots.

Starts/stops individual bot instances when agents configure their Telegram token.
Loads all configured bots on startup.
"""

import uuid
import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.telegram.agent_bot import TelegramAgentBot


class TelegramBotManager:
    """Manages multiple TelegramAgentBot instances, one per agent."""

    def __init__(self):
        self._bots: dict[str, TelegramAgentBot] = {}  # agent_id -> bot
        self._retry_tasks: dict[str, asyncio.Task] = {}

    async def start_bot(self, agent_id: str, agent_name: str, bot_token: str, auth_key: str) -> None:
        """Start a Telegram bot for an agent."""
        # Stop existing bot for this agent if any
        await self.stop_bot(agent_id)

        bot = TelegramAgentBot(agent_id, agent_name, bot_token, auth_key)
        try:
            await bot.start()
            self._bots[agent_id] = bot
            self._retry_tasks.pop(agent_id, None)
        except Exception as e:
            print(f"[Telegram] Failed to start bot for {agent_name}: {e}")
            raise

    async def stop_bot(self, agent_id: str) -> None:
        """Stop the Telegram bot for an agent."""
        retry_task = self._retry_tasks.pop(agent_id, None)
        if retry_task and retry_task is not asyncio.current_task():
            retry_task.cancel()
        if agent_id in self._bots:
            await self._bots[agent_id].stop()
            del self._bots[agent_id]

    async def stop_all(self) -> None:
        """Stop all running agent bots."""
        for agent_id in list(self._bots.keys()):
            await self.stop_bot(agent_id)

    def is_running(self, agent_id: str) -> bool:
        return agent_id in self._bots and self._bots[agent_id]._started

    def get_bot(self, agent_id: str) -> TelegramAgentBot | None:
        return self._bots.get(agent_id)

    async def load_all_from_db(self, db: AsyncSession) -> None:
        """Load and start bots for all agents that have telegram configured."""
        result = await db.execute(select(Agent))
        agents = result.scalars().all()

        for agent in agents:
            config = agent.config or {}
            bot_token = config.get("telegram_bot_token")
            auth_key = config.get("telegram_auth_key")
            if bot_token and auth_key:
                try:
                    await self.start_bot(agent.id, agent.name, bot_token, auth_key)
                except Exception as e:
                    print(f"[Telegram] Bot for {agent.name} not started yet: {e}")
                    self._schedule_retry(agent.id, agent.name, bot_token, auth_key)

    def _schedule_retry(self, agent_id: str, agent_name: str, bot_token: str, auth_key: str) -> None:
        """Retry bot startup after transient network or Telegram API failures."""
        if agent_id in self._retry_tasks:
            return

        async def retry_loop() -> None:
            delay = 15
            while agent_id not in self._bots:
                await asyncio.sleep(delay)
                try:
                    await self.start_bot(agent_id, agent_name, bot_token, auth_key)
                    return
                except Exception as e:
                    print(f"[Telegram] Retry failed for {agent_name}: {e}")
                    delay = min(delay * 2, 300)

        self._retry_tasks[agent_id] = asyncio.create_task(retry_loop())


def generate_auth_key() -> str:
    """Generate a short, user-friendly auth key."""
    return uuid.uuid4().hex[:16].upper()
