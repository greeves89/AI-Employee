from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.dependencies import require_admin, require_auth
from app.models.oauth_integration import OAuthIntegration, OAuthProvider
from app.schemas.settings import SettingsResponse, SettingsUpdate
from app.services.settings_service import SettingsService

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/", response_model=SettingsResponse)
async def get_settings(user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    has_api_key = bool(settings.anthropic_api_key)

    # Check DB for Anthropic OAuth integration (bot's own session)
    result = await db.execute(
        select(OAuthIntegration).where(OAuthIntegration.provider == OAuthProvider.ANTHROPIC)
    )
    anthropic_integration = result.scalar_one_or_none()
    has_oauth_token = anthropic_integration is not None or bool(settings.claude_code_oauth_token)

    if has_api_key:
        auth_method = "api_key"
    elif has_oauth_token:
        auth_method = "oauth_token"
    else:
        auth_method = "none"

    return SettingsResponse(
        has_api_key=has_api_key,
        has_oauth_token=has_oauth_token,
        has_oauth_refresh_token=bool(settings.claude_code_oauth_refresh_token),
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
        # OAuth integrations
        has_google_oauth=bool(settings.oauth_google_client_id),
        has_microsoft_oauth=bool(settings.oauth_microsoft_client_id),
        has_apple_oauth=bool(settings.oauth_apple_client_id),
        # Lifecycle
        agent_idle_timeout_minutes=int(await SettingsService(db).get("agent_idle_timeout_minutes") or "30"),
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
    # OAuth integration credentials
    "oauth_google_client_id": "oauth_google_client_id",
    "oauth_google_client_secret": "oauth_google_client_secret",
    "oauth_microsoft_client_id": "oauth_microsoft_client_id",
    "oauth_microsoft_client_secret": "oauth_microsoft_client_secret",
    "oauth_apple_client_id": "oauth_apple_client_id",
    "oauth_apple_team_id": "oauth_apple_team_id",
    "oauth_apple_key_id": "oauth_apple_key_id",
    "oauth_apple_private_key": "oauth_apple_private_key",
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
    if data.claude_oauth_refresh_token is not None:
        settings.claude_code_oauth_refresh_token = data.claude_oauth_refresh_token
        await svc.set("claude_code_oauth_refresh_token", data.claude_oauth_refresh_token)

    # Telegram
    if data.telegram is not None:
        settings.telegram_bot_token = data.telegram.bot_token
        settings.telegram_chat_id = data.telegram.chat_id
        await svc.set("telegram_bot_token", data.telegram.bot_token)
        await svc.set("telegram_chat_id", data.telegram.chat_id)

    # Lifecycle: agent idle timeout (0 = never stop)
    if data.agent_idle_timeout_minutes is not None:
        if data.agent_idle_timeout_minutes < 0:
            data.agent_idle_timeout_minutes = 0
        await svc.set("agent_idle_timeout_minutes", str(data.agent_idle_timeout_minutes))

    await db.commit()
    return {"status": "updated"}
