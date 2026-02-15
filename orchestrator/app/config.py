from pydantic_settings import BaseSettings

# Bump this when the agent image changes and agents need updating
AGENT_VERSION = "1.2.0"


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

    # OAuth Integrations
    oauth_google_client_id: str = ""
    oauth_google_client_secret: str = ""
    oauth_microsoft_client_id: str = ""
    oauth_microsoft_client_secret: str = ""
    oauth_apple_client_id: str = ""
    oauth_apple_team_id: str = ""
    oauth_apple_key_id: str = ""
    oauth_apple_private_key: str = ""
    oauth_redirect_base_url: str = "http://localhost:8000"

    model_config = {"env_prefix": "", "case_sensitive": False}


settings = Settings()
