from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.load_balancer import LoadBalancer
from app.core.task_router import TaskRouter
from app.db.session import get_db
from app.dependencies import get_redis_service
from app.models.task import TaskStatus
from app.schemas.task import TaskCreate, TaskListResponse, TaskResponse
from app.services.redis_service import RedisService

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _get_task_router(
    db: AsyncSession = Depends(get_db),
    redis: RedisService = Depends(get_redis_service),
) -> TaskRouter:
    lb = LoadBalancer(redis)
    return TaskRouter(db, redis, lb)


@router.get("/", response_model=TaskListResponse)
async def list_tasks(
    status: TaskStatus | None = None,
    agent_id: str | None = None,
    router_: TaskRouter = Depends(_get_task_router),
):
    tasks = await router_.list_tasks(status=status, agent_id=agent_id)
    return TaskListResponse(
        tasks=[TaskResponse.model_validate(t) for t in tasks],
        total=len(tasks),
    )


@router.post("/", response_model=TaskResponse, status_code=201)
async def create_task(
    data: TaskCreate,
    router_: TaskRouter = Depends(_get_task_router),
):
    task = await router_.create_and_route_task(
        title=data.title,
        prompt=data.prompt,
        priority=data.priority,
        agent_id=data.agent_id,
        model=data.model,
    )
    return TaskResponse.model_validate(task)


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: str,
    router_: TaskRouter = Depends(_get_task_router),
):
    task = await router_.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskResponse.model_validate(task)
