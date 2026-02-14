"""Task service - thin wrapper delegating to TaskRouter for DI convenience."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.load_balancer import LoadBalancer
from app.core.task_router import TaskRouter
from app.models.task import Task, TaskStatus
from app.services.redis_service import RedisService


class TaskService:
    def __init__(self, db: AsyncSession, redis: RedisService):
        lb = LoadBalancer(redis)
        self.router = TaskRouter(db, redis, lb)

    async def create(
        self,
        title: str,
        prompt: str,
        priority: int = 1,
        agent_id: str | None = None,
        model: str | None = None,
    ) -> Task:
        return await self.router.create_and_route_task(
            title=title,
            prompt=prompt,
            priority=priority,
            agent_id=agent_id,
            model=model,
        )

    async def list_all(
        self, status: TaskStatus | None = None, agent_id: str | None = None
    ) -> list[Task]:
        return await self.router.list_tasks(status=status, agent_id=agent_id)

    async def get(self, task_id: str) -> Task | None:
        return await self.router.get_task(task_id)
