from datetime import datetime

from pydantic import BaseModel

from app.models.agent import AgentState


class AgentCreate(BaseModel):
    name: str
    model: str | None = None
    role: str | None = None
    integrations: list[str] | None = None
    permissions: list[str] | None = None
    budget_usd: float | None = None


class AgentResponse(BaseModel):
    id: str
    name: str
    container_id: str | None
    state: AgentState
    model: str
    role: str | None = None
    onboarding_complete: bool = False
    integrations: list[str] = []
    permissions: list[str] = []
    update_available: bool = False
    budget_usd: float | None = None
    total_cost_usd: float = 0.0
    user_id: str | None = None
    created_at: datetime
    updated_at: datetime

    # Live metrics (from Redis, not DB)
    current_task: str | None = None
    cpu_percent: float | None = None
    memory_usage_mb: float | None = None
    queue_depth: int | None = None

    model_config = {"from_attributes": True}


class AgentListResponse(BaseModel):
    agents: list[AgentResponse]
    total: int


class KnowledgeResponse(BaseModel):
    knowledge: str
    metrics: dict = {}


class KnowledgeUpdate(BaseModel):
    content: str
