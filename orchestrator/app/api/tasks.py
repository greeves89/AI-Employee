from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.load_balancer import LoadBalancer
from app.core.pricing import estimate_prompt_cost
from app.core.task_router import TaskRouter
from app.db.session import get_db
from app.dependencies import get_redis_service, require_auth
from app.models.task import TaskStatus
from app.schemas.task import TaskCreate, TaskListResponse, TaskResponse
from app.services.redis_service import RedisService

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _get_task_router(
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: RedisService = Depends(get_redis_service),
) -> TaskRouter:
    lb = LoadBalancer(redis)
    docker = getattr(request.app.state, "docker", None)
    return TaskRouter(db, redis, lb, docker_service=docker)


@router.get("/", response_model=TaskListResponse)
async def list_tasks(
    status: TaskStatus | None = None,
    agent_id: str | None = None,
    user=Depends(require_auth),
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
    user=Depends(require_auth),
    router_: TaskRouter = Depends(_get_task_router),
):
    from app.models.user import UserRole
    if user.role == UserRole.VIEWER:
        raise HTTPException(status_code=403, detail="Viewers cannot create tasks")
    task = await router_.create_and_route_task(
        title=data.title,
        prompt=data.prompt,
        priority=data.priority,
        agent_id=data.agent_id,
        model=data.model,
        parent_task_id=data.parent_task_id,
    )
    return TaskResponse.model_validate(task)


class TaskEstimateRequest(BaseModel):
    prompt: str
    model: str | None = None
    agent_id: str | None = None


class TaskEstimateResponse(BaseModel):
    estimated_input_tokens: int
    model: str
    min_usd: float
    avg_usd: float
    max_usd: float
    agent_avg_usd: float | None = None  # Historical average for this agent


@router.post("/estimate", response_model=TaskEstimateResponse)
async def estimate_task_cost(
    data: TaskEstimateRequest,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Estimate the cost of a task before execution.

    Combines model pricing with historical agent performance data.
    """
    model = data.model or settings.default_model
    estimate = estimate_prompt_cost(data.prompt, model)

    # If agent specified, get historical average cost
    agent_avg = None
    if data.agent_id:
        from app.models.agent import Agent

        result = await db.execute(select(Agent).where(Agent.id == data.agent_id))
        agent = result.scalar_one_or_none()
        if agent and agent.config:
            metrics = agent.config.get("metrics", {})
            total_cost = agent.config.get("total_cost_usd", 0)
            total_tasks = metrics.get("total", 0)
            if total_tasks > 0:
                agent_avg = round(total_cost / total_tasks, 6)

    return TaskEstimateResponse(
        **estimate,
        agent_avg_usd=agent_avg,
    )


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: str,
    user=Depends(require_auth),
    router_: TaskRouter = Depends(_get_task_router),
):
    task = await router_.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskResponse.model_validate(task)


@router.delete("/{task_id}")
async def delete_task(
    task_id: str,
    user=Depends(require_auth),
    router_: TaskRouter = Depends(_get_task_router),
):
    try:
        deleted = await router_.delete_task(task_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not deleted:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"ok": True}


@router.post("/{task_id}/cancel", response_model=TaskResponse)
async def cancel_task(
    task_id: str,
    user=Depends(require_auth),
    router_: TaskRouter = Depends(_get_task_router),
):
    try:
        task = await router_.cancel_task(task_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskResponse.model_validate(task)
