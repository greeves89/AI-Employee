from pydantic import BaseModel


class TelegramConfig(BaseModel):
    bot_token: str
    chat_id: str


class SettingsUpdate(BaseModel):
    anthropic_api_key: str | None = None
    claude_oauth_token: str | None = None
    telegram: TelegramConfig | None = None
    default_model: str | None = None
    max_turns: int | None = None
    max_agents: int | None = None


class SettingsResponse(BaseModel):
    has_api_key: bool
    has_oauth_token: bool
    auth_method: str  # "api_key", "oauth_token", or "none"
    has_telegram: bool
    default_model: str
    max_turns: int
    max_agents: int
