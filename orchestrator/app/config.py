from pydantic_settings import BaseSettings

# Bump this when the agent image changes and agents need updating
AGENT_VERSION = "1.17.0"


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://ai_employee:devpassword@postgres:5432/ai_employee"

    # Redis
    redis_url: str = "redis://redis:6379"
    redis_url_internal: str = "redis://redis:6379"

    # Claude Authentication (either API key OR OAuth token)
    anthropic_api_key: str = ""
    claude_code_oauth_token: str = ""
    claude_code_oauth_refresh_token: str = ""
    default_model: str = "claude-sonnet-4-6"
    max_turns: int = 100
    extended_thinking: bool = False  # Thinking is model-controlled, not a CLI flag

    # Model Provider: "anthropic", "bedrock", "vertex", "foundry"
    model_provider: str = "anthropic"

    # Amazon Bedrock
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "us-east-1"

    # Google Vertex AI
    vertex_project_id: str = ""
    vertex_region: str = "us-east5"
    vertex_credentials_json: str = ""

    # Microsoft Foundry (Azure)
    foundry_api_key: str = ""
    foundry_resource: str = ""

    # Docker
    agent_image: str = "ai-employee-agent:latest"
    agent_network: str = "ai-employee-network"
    max_agents: int = 10
    agent_memory_limit: str = "2g"
    agent_cpu_quota: int = 100000  # 1 CPU

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # OpenAI (for Whisper voice transcription + TTS)
    openai_api_key: str = ""

    # Security
    encryption_key: str = ""
    api_secret_key: str = "change-me-in-production"  # Used for agent HMAC tokens + JWT signing
    registration_open: bool = True  # Allow new user registration

    # OAuth Integrations
    oauth_google_client_id: str = ""
    oauth_google_client_secret: str = ""
    oauth_microsoft_client_id: str = ""
    oauth_microsoft_client_secret: str = ""
    oauth_github_client_id: str = ""
    oauth_github_client_secret: str = ""
    oauth_apple_client_id: str = ""
    oauth_apple_team_id: str = ""
    oauth_apple_key_id: str = ""
    oauth_apple_private_key: str = ""
    # Anthropic OAuth (Claude Code public client — no secret needed)
    oauth_anthropic_client_id: str = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
    oauth_redirect_base_url: str = "http://localhost:8000"

    # GitHub Webhook
    github_webhook_secret: str = ""  # Set to verify GitHub webhook signatures

    model_config = {"env_prefix": "", "case_sensitive": False}


settings = Settings()
