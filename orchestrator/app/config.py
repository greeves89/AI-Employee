from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://ai_employee:devpassword@postgres:5432/ai_employee"

    # Redis
    redis_url: str = "redis://redis:6379"
    redis_url_internal: str = "redis://redis:6379"

    # Claude Authentication (either API key OR OAuth token)
    anthropic_api_key: str = ""
    claude_code_oauth_token: str = ""
    default_model: str = "claude-sonnet-4-5-20250929"
    max_turns: int = 100

    # Docker
    agent_image: str = "ai-employee-agent:latest"
    agent_network: str = "ai-employee-network"
    max_agents: int = 10
    agent_memory_limit: str = "2g"
    agent_cpu_quota: int = 100000  # 1 CPU

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Security
    encryption_key: str = ""

    model_config = {"env_prefix": "", "case_sensitive": False}


settings = Settings()
