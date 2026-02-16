from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.dependencies import require_admin, require_auth
from app.schemas.settings import SettingsResponse, SettingsUpdate
from app.services.settings_service import SettingsService

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/", response_model=SettingsResponse)
async def get_settings(user=Depends(require_auth)):
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
        registration_open=settings.registration_open,
        # Provider info
        model_provider=settings.model_provider,
        has_bedrock=bool(settings.aws_access_key_id and settings.aws_secret_access_key),
        has_vertex=bool(settings.vertex_project_id and settings.vertex_credentials_json),
        has_foundry=bool(settings.foundry_api_key and settings.foundry_resource),
        aws_region=settings.aws_region,
        vertex_region=settings.vertex_region,
        foundry_resource=settings.foundry_resource,
    )


# Mapping from SettingsUpdate field names to config attribute names
_FIELD_MAP: dict[str, str] = {
    "model_provider": "model_provider",
    "default_model": "default_model",
    "max_turns": "max_turns",
    "max_agents": "max_agents",
    "registration_open": "registration_open",
    "anthropic_api_key": "anthropic_api_key",
    "aws_access_key_id": "aws_access_key_id",
    "aws_secret_access_key": "aws_secret_access_key",
    "aws_region": "aws_region",
    "vertex_project_id": "vertex_project_id",
    "vertex_region": "vertex_region",
    "vertex_credentials_json": "vertex_credentials_json",
    "foundry_api_key": "foundry_api_key",
    "foundry_resource": "foundry_resource",
}


@router.patch("/")
async def update_settings(
    data: SettingsUpdate,
    user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    svc = SettingsService(db)

    # Handle simple mapped fields
    for field_name, config_attr in _FIELD_MAP.items():
        value = getattr(data, field_name, None)
        if value is not None:
            setattr(settings, config_attr, value)
            await svc.set(config_attr, str(value))

    # Anthropic: API key / OAuth are mutually exclusive
    if data.anthropic_api_key:
        settings.claude_code_oauth_token = ""
        await svc.set("claude_code_oauth_token", "")
    if data.claude_oauth_token is not None:
        settings.claude_code_oauth_token = data.claude_oauth_token
        await svc.set("claude_code_oauth_token", data.claude_oauth_token)
        if data.claude_oauth_token:
            settings.anthropic_api_key = ""
            await svc.set("anthropic_api_key", "")

    # Telegram
    if data.telegram is not None:
        settings.telegram_bot_token = data.telegram.bot_token
        settings.telegram_chat_id = data.telegram.chat_id
        await svc.set("telegram_bot_token", data.telegram.bot_token)
        await svc.set("telegram_chat_id", data.telegram.chat_id)

    await db.commit()
    return {"status": "updated"}
