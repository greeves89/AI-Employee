"""Task service - thin wrapper delegating to TaskRouter for DI convenience."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.load_balancer import LoadBalancer
from app.core.task_router import TaskRouter, _compute_auto_rating
from app.models.task import Task, TaskStatus
from app.services.redis_service import RedisService


async def auto_rate_task(task: Task, db: AsyncSession) -> int:
    """Compute and persist an automatic rating for a completed/failed task.

    Returns the computed rating (1-5).  Raises if the task has no agent_id
    or is not in a terminal status.
    """
    from app.models.task_rating import TaskRating

    if task.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED):
        raise ValueError(f"Cannot auto-rate task {task.id} with status '{task.status.value}'")
    if not task.agent_id:
        raise ValueError(f"Cannot auto-rate task {task.id}: no agent_id")

    rating = _compute_auto_rating(task)
    task_rating = TaskRating(
        task_id=task.id,
        agent_id=task.agent_id,
        user_id=None,
        rating=rating,
        comment="auto-rated",
        task_cost_usd=task.cost_usd,
        task_duration_ms=task.duration_ms,
        task_num_turns=task.num_turns,
    )
    db.add(task_rating)
    await db.commit()
    return rating


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
