from pydantic import BaseModel


class TelegramConfig(BaseModel):
    bot_token: str
    chat_id: str


class SettingsUpdate(BaseModel):
    anthropic_api_key: str | None = None
    claude_oauth_token: str | None = None
    claude_oauth_refresh_token: str | None = None
    telegram: TelegramConfig | None = None
    default_model: str | None = None
    max_turns: int | None = None
    max_agents: int | None = None
    registration_open: bool | None = None
    # Provider
    model_provider: str | None = None
    # Bedrock
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    aws_region: str | None = None
    # Vertex AI
    vertex_project_id: str | None = None
    vertex_region: str | None = None
    vertex_credentials_json: str | None = None
    # Microsoft Foundry
    foundry_api_key: str | None = None
    foundry_resource: str | None = None
    # OAuth integration credentials
    oauth_google_client_id: str | None = None
    oauth_google_client_secret: str | None = None
    oauth_microsoft_client_id: str | None = None
    oauth_microsoft_client_secret: str | None = None
    oauth_apple_client_id: str | None = None
    oauth_apple_team_id: str | None = None
    oauth_apple_key_id: str | None = None
    oauth_apple_private_key: str | None = None
    # Lifecycle config
    agent_idle_timeout_minutes: int | None = None  # 0 = never auto-stop


class SettingsResponse(BaseModel):
    has_api_key: bool
    has_oauth_token: bool
    has_oauth_refresh_token: bool
    auth_method: str  # "api_key", "oauth_token", or "none"
    has_telegram: bool
    default_model: str
    max_turns: int
    max_agents: int
    registration_open: bool
    # Provider
    model_provider: str
    has_bedrock: bool
    has_vertex: bool
    has_foundry: bool
    aws_region: str
    vertex_region: str
    foundry_resource: str
    # OAuth integrations
    has_google_oauth: bool = False
    has_microsoft_oauth: bool = False
    has_apple_oauth: bool = False
    # Lifecycle
    agent_idle_timeout_minutes: int = 30
