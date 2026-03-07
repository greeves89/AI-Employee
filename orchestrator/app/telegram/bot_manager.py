"""Manager for per-agent Telegram bots.

Starts/stops individual bot instances when agents configure their Telegram token.
Loads all configured bots on startup.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.telegram.agent_bot import TelegramAgentBot


class TelegramBotManager:
    """Manages multiple TelegramAgentBot instances, one per agent."""

    def __init__(self):
        self._bots: dict[str, TelegramAgentBot] = {}  # agent_id -> bot

    async def start_bot(self, agent_id: str, agent_name: str, bot_token: str, auth_key: str) -> None:
        """Start a Telegram bot for an agent."""
        # Stop existing bot for this agent if any
        await self.stop_bot(agent_id)

        bot = TelegramAgentBot(agent_id, agent_name, bot_token, auth_key)
        try:
            await bot.start()
            self._bots[agent_id] = bot
        except Exception as e:
            print(f"[Telegram] Failed to start bot for {agent_name}: {e}")
            raise

    async def stop_bot(self, agent_id: str) -> None:
        """Stop the Telegram bot for an agent."""
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
                    print(f"[Telegram] Skipping bot for {agent.name}: {e}")


def generate_auth_key() -> str:
    """Generate a short, user-friendly auth key."""
    return uuid.uuid4().hex[:16].upper()
