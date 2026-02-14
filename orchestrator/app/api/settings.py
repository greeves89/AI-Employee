from fastapi import APIRouter

from app.config import settings
from app.schemas.settings import SettingsResponse, SettingsUpdate

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/", response_model=SettingsResponse)
async def get_settings():
    has_api_key = bool(settings.anthropic_api_key)
    has_oauth_token = bool(settings.claude_code_oauth_token)

    if has_api_key:
        auth_method = "api_key"
    elif has_oauth_token:
        auth_method = "oauth_token"
    else:
        auth_method = "none"

    return SettingsResponse(
        has_api_key=has_api_key,
        has_oauth_token=has_oauth_token,
        auth_method=auth_method,
        has_telegram=bool(settings.telegram_bot_token),
        default_model=settings.default_model,
        max_turns=settings.max_turns,
        max_agents=settings.max_agents,
    )


@router.patch("/")
async def update_settings(data: SettingsUpdate):
    # In a production app, these would be persisted to DB
    # For now, we update the in-memory settings
    if data.default_model is not None:
        settings.default_model = data.default_model
    if data.max_turns is not None:
        settings.max_turns = data.max_turns
    if data.max_agents is not None:
        settings.max_agents = data.max_agents
    if data.anthropic_api_key is not None:
        settings.anthropic_api_key = data.anthropic_api_key
        # Clear OAuth token when API key is set (use one method at a time)
        if data.anthropic_api_key:
            settings.claude_code_oauth_token = ""
    if data.claude_oauth_token is not None:
        settings.claude_code_oauth_token = data.claude_oauth_token
        # Clear API key when OAuth token is set (use one method at a time)
        if data.claude_oauth_token:
            settings.anthropic_api_key = ""
    if data.telegram is not None:
        settings.telegram_bot_token = data.telegram.bot_token
        settings.telegram_chat_id = data.telegram.chat_id

    return {"status": "updated"}
