import os
import pathlib
from pydantic_settings import BaseSettings

def _read_version() -> str:
    # Env var wins (set via docker-compose)
    if v := os.environ.get("AGENT_VERSION", "").strip():
        return v
    # Local dev: walk up from orchestrator/app/config.py to repo root
    for candidate in [
        pathlib.Path(__file__).parent.parent.parent / "VERSION",
        pathlib.Path(__file__).parent.parent / "VERSION",
    ]:
        if candidate.exists():
            v = candidate.read_text().strip()
            if v:
                return v
    return "1.29.0"

AGENT_VERSION = _read_version()


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

    # Platform-wide spending cap (0 = unlimited)
    platform_budget_usd: float = 0.0

    # Docker
    agent_image: str = "ai-employee-agent:latest"
    agent_network: str = "ai-employee-network"
    max_agents: int = 10
    agent_memory_limit: str = "4g"
    agent_cpu_quota: int = 200000  # 2 CPUs
    agent_workspace_size_gb: float = 10.0
    # Admin-defined mount catalog: newline-separated entries
    # Format per line: label:host_path:container_path:mode  (mode = ro | rw)
    # Example: nas-docs:/mnt/nas/docs:/mnt/docs:ro
    agent_mount_catalog: str = ""

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # OpenAI (for Whisper voice transcription)
    openai_api_key: str = ""

    # Local TTS service (VibeVoice, runs natively on Mac host with Metal GPU)
    # Docker containers reach Mac host via host.docker.internal
    tts_service_url: str = "http://host.docker.internal:8002"

    # Security
    encryption_key: str = ""
    api_secret_key: str = "change-me-in-production"  # Used for agent HMAC tokens + JWT signing
    registration_open: bool = True  # Allow new user registration
    setup_token: str = ""  # Required for first admin registration (if set)

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

    # GitHub API token for skill crawler (avoids 60 req/h rate limit → 5000 req/h)
    github_token: str = ""

    # Skill file attachments — stored on shared Docker volume
    skill_files_root: str = "/shared/skill-files"

    model_config = {"env_prefix": "", "case_sensitive": False}


settings = Settings()
