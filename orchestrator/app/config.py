import os
import pathlib
from pydantic_settings import BaseSettings

def _read_version() -> str:
    for candidate in [
        pathlib.Path("/VERSION"),                                      # Docker: mounted from repo root
        pathlib.Path(__file__).parent.parent.parent / "VERSION",      # Local dev: repo root
        pathlib.Path(__file__).parent.parent / "VERSION",             # Fallback
    ]:
        if candidate.exists():
            v = candidate.read_text().strip()
            if v:
                return v
    return "0.0.0"

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
    # How many DIFFERENT chat sessions one agent processes concurrently. 1 = serial
    # (proven default). >1 lets independent sessions/tasks run in parallel in one
    # container (each its own claude/codex/custom-LLM turn); same session stays serial.
    max_parallel_chats: int = 1
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
    agent_memory_limit: str = "8g"  # 8g: video renders/builds need >4g (4g forces low-memory render, 1 worker, slow)
    agent_cpu_quota: int = 200000  # 2 CPUs
    agent_workspace_size_gb: float = 10.0
    # Admin-defined mount catalog: newline-separated entries
    # Format per line: label:host_path:container_path:mode  (mode = ro | rw)
    # Example: nas-docs:/mnt/nas/docs:/mnt/docs:ro
    agent_mount_catalog: str = ""
    # Second Brains: department-shared knowledge vaults (DB-managed mount entries).
    # Host base dir (must be bind-mounted into the orchestrator rw for provisioning)
    # and the container path prefix where each brain is mounted in agents.
    secondbrain_base: str = "/srv/secondbrain"

    # Kiosk (local-only on-device status display on the Raspberry Pi)
    # The kiosk router is UNAUTHENTICATED by design (local Pi browser). It must
    # only be mounted on an actual kiosk device. Default off so non-kiosk
    # deployments (e.g. a shared VPS) never expose the unauthenticated router.
    kiosk_enabled: bool = False
    # Live electricity price for the power-cost estimate (env: ELECTRICITY_PRICE_EUR_KWH).
    electricity_price_eur_kwh: float = 0.35
    # Host metrics JSON written by the host power collector (bind-mounted read-only).
    kiosk_metrics_path: str = "/kiosk-metrics/metrics.json"
    secondbrain_container_base: str = "/mnt/brains"

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # OpenAI (for Whisper voice transcription)
    openai_api_key: str = ""

    # Local TTS service (VibeVoice, runs natively on Mac host with Metal GPU)
    # Docker containers reach Mac host via host.docker.internal
    tts_service_url: str = "http://host.docker.internal:8002"

    # Local STT service (faster-whisper) — transcribes Telegram voice messages.
    # Runs as a compose service on the internal network.
    stt_service_url: str = "http://stt-service:8003"

    # Security
    encryption_key: str = ""
    api_secret_key: str = "change-me-in-production"  # Used for agent HMAC tokens + JWT signing
    registration_open: bool = True  # Allow new user registration
    # When True, new self-registered users (SSO or password) land in "pending approval"
    # (approved=False) and must be unlocked by an admin before they can use the app
    # (OpenWebUI-style "Warten auf Freischaltung"). Default off. Admin-created users are
    # always approved.
    require_user_approval: bool = False
    setup_token: str = ""  # Required for first admin registration (if set)
    # SSO-only: disable password login entirely → only Microsoft SSO (MFA) can sign in.
    # Closes the "knows the password → impersonate" vector. Per-deployment toggle.
    sso_only_login: bool = False
    # BREAK-GLASS (env only, NOT in DB): re-enable password login even if sso_only_login
    # is on — emergency access if SSO is misconfigured / admins are locked out.
    emergency_password_login: bool = False
    # Delete the user's stored MS Graph token on logout (no persistent token after
    # sign-out). Trade-off: autonomous agents lose Graph until the owner re-connects.
    revoke_msgraph_on_logout: bool = False

    # OAuth Integrations
    # Microsoft Entra tenant for SSO + Graph. "common" works only for multi-tenant
    # apps; single-tenant apps MUST use their tenant id (else AADSTS50194 on /common).
    oauth_microsoft_tenant_id: str = "common"
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

    # Expose the MS Graph MCP server to external LLM clients (e.g. OpenWebUI) via
    # our built-in OAuth 2.1 AS. Admin-only; only effective when a Microsoft app
    # registration (oauth_microsoft_client_id) is configured. Default OFF.
    msgraph_mcp_external_enabled: bool = False
    # Optional independent signing key for MCP access tokens. Empty = derive from
    # api_secret_key (domain-separated). Set for full key isolation from sessions.
    mcp_signing_key: str = ""

    # GitHub Webhook
    github_webhook_secret: str = ""  # Set to verify GitHub webhook signatures

    # Feedback Webhook (optional)
    # Sends newly submitted app feedback to an external workflow system.
    feedback_webhook_url: str = ""
    feedback_webhook_api_key: str = ""

    # GitHub API token for skill crawler (avoids 60 req/h rate limit → 5000 req/h)
    github_token: str = ""

    # Skill file attachments — stored on shared Docker volume
    skill_files_root: str = "/shared/skill-files"

    # APNs push notifications (iOS app)
    apns_auth_key: str = ""      # contents of the .p8 file
    apns_key_id: str = ""
    apns_team_id: str = ""
    apns_bundle_id: str = "com.da.ai-employee-ios.ai-employee-ios"
    apns_sandbox: bool = True    # development builds use the sandbox gateway

    model_config = {"env_prefix": "", "case_sensitive": False}


settings = Settings()
