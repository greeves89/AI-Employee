"""Agent service - thin wrapper delegating to AgentManager for DI convenience."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_manager import AgentManager
from app.models.agent import Agent
from app.services.docker_service import DockerService
from app.services.redis_service import RedisService


class AgentService:
    def __init__(self, db: AsyncSession, docker: DockerService, redis: RedisService):
        self.manager = AgentManager(db, docker, redis)

    async def create(self, name: str, model: str | None = None) -> Agent:
        return await self.manager.create_agent(name, model)

    async def list_all(self) -> list[dict]:
        agents = await self.manager.list_agents()
        results = []
        for agent in agents:
            metrics = await self.manager.get_agent_with_metrics(agent.id)
            results.append(metrics)
        return results

    async def get(self, agent_id: str) -> dict:
        return await self.manager.get_agent_with_metrics(agent_id)

    async def stop(self, agent_id: str) -> Agent:
        return await self.manager.stop_agent(agent_id)

    async def start(self, agent_id: str) -> Agent:
        return await self.manager.start_agent(agent_id)

    async def remove(self, agent_id: str, remove_data: bool = False) -> None:
        return await self.manager.remove_agent(agent_id, remove_data)
