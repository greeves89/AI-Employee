from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.models.agent import AgentState

AgentMode = Literal["claude_code", "custom_llm"]
LLMProviderType = Literal["openai", "anthropic", "google", "ollama", "lm-studio"]


ThinkingMode = Literal["off", "auto", "on"]


class LLMConfig(BaseModel):
    """Configuration for a custom LLM provider."""
    provider_type: LLMProviderType
    api_endpoint: str
    api_key: str  # plaintext on input, encrypted in DB, never in response
    model_name: str
    max_tokens: int = 4096
    temperature: float = 0.7
    system_prompt: str = ""
    tools_enabled: bool = True
    thinking_mode: ThinkingMode = "auto"  # "off"=never, "auto"=model decides, "on"=always


class LLMConfigUpdate(BaseModel):
    """Partial update for LLM config (all fields optional)."""
    api_endpoint: str | None = None
    api_key: str | None = None  # only set if user wants to change it
    model_name: str | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    system_prompt: str | None = None
    tools_enabled: bool | None = None


class LLMConfigResponse(BaseModel):
    """LLM config as returned in API (no API key!)."""
    provider_type: str
    api_endpoint: str
    model_name: str
    max_tokens: int
    temperature: float
    system_prompt: str
    tools_enabled: bool
    thinking_mode: str = "auto"


class AgentCreate(BaseModel):
    name: str
    model: str | None = None
    role: str | None = None
    integrations: list[str] | None = None
    permissions: list[str] | None = None
    budget_usd: float | None = None
    mode: AgentMode = "claude_code"
    llm_config: LLMConfig | None = None  # required when mode == "custom_llm"


class AgentResponse(BaseModel):
    id: str
    name: str
    container_id: str | None
    state: AgentState
    model: str
    model_provider: str = "anthropic"
    mode: str = "claude_code"
    llm_config: LLMConfigResponse | None = None
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


class AgentModelUpdate(BaseModel):
    """Update agent's model provider and model."""
    model_provider: str  # "anthropic", "bedrock", "vertex", "foundry"
    model: str  # e.g., "claude-sonnet-4-6"


class KnowledgeResponse(BaseModel):
    knowledge: str
    metrics: dict = {}


class KnowledgeUpdate(BaseModel):
    content: str
