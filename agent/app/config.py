from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    agent_id: str = "agent-001"
    agent_name: str = ""
    agent_token: str = ""
    redis_url: str = "redis://redis:6379"
    health_port: int = 8080
    default_model: str = "claude-sonnet-4-6"
    max_turns: int = 100
    extended_thinking: bool = True  # Enable extended thinking by default
    anthropic_api_key: str = ""
    claude_code_oauth_token: str = ""
    workspace_dir: str = "/workspace"
    orchestrator_url: str = "http://orchestrator:8000"

    model_config = {"env_prefix": "", "case_sensitive": False}


settings = Settings()
