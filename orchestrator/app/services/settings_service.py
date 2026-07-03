import base64
import logging

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.platform_settings import PlatformSettings

logger = logging.getLogger(__name__)

# Settings keys that contain sensitive data and must be encrypted
SECRET_KEYS = {
    "anthropic_api_key",
    "claude_code_oauth_token",
    "claude_code_oauth_refresh_token",
    "aws_access_key_id",
    "aws_secret_access_key",
    "vertex_credentials_json",
    "foundry_api_key",
    "telegram_bot_token",
    # OAuth integration credentials
    "oauth_google_client_id",
    "oauth_google_client_secret",
    "oauth_microsoft_client_id",
    "oauth_microsoft_client_secret",
    "oauth_apple_client_id",
    "oauth_apple_private_key",
    # Voice provider API keys
    "voice_openai_api_key",
    "voice_elevenlabs_api_key",
    "voice_azure_speech_key",
    # APNs auth key (.p8 contents)
    "apns_auth_key",
    # On-prem Exchange: service-account password (basic/NTLM) — secret
    "exchange_service_account_password",
}

# All settings keys that can be persisted
ALLOWED_KEYS = SECRET_KEYS | {
    "model_provider",
    "default_model",
    "max_turns",
    "max_agents",
    "aws_region",
    "vertex_project_id",
    "vertex_region",
    "foundry_resource",
    "telegram_chat_id",
    "registration_open",
    "sso_only_login",
    "require_user_approval",
    "revoke_msgraph_on_logout",
    # OAuth non-secret fields
    "oauth_microsoft_tenant_id",
    "oauth_apple_team_id",
    "oauth_apple_key_id",
    # License
    "license_key",
    # Lifecycle configuration
    "agent_idle_timeout_minutes",
    # Expose MS Graph MCP server to external LLM clients (OpenWebUI)
    "msgraph_mcp_external_enabled",
    # Improvement engine thresholds
    "improvement_suggestion_model",
    "improvement_min_ratings",
    "improvement_suggestion_threshold",
    "improvement_min_skill_usages",
    "improvement_skill_threshold",
    "improvement_analysis_interval",
    # Voice provider config
    "voice_stt_provider",
    "voice_tts_provider",
    "voice_tts_voice",
    "voice_llm_model",
    "voice_language",
    "voice_azure_speech_region",
    # Realtime voice front (Nova Sonic): platform-wide default + voice id + account
    "voice_interaction_model",
    "voice_interaction_account_id",
    "nova_sonic_voice",
    # APNs push config
    "apns_key_id",
    "apns_team_id",
    "apns_bundle_id",
    "apns_sandbox",
    # On-prem Exchange (EWS) — admin connection config (per-user auth via impersonation)
    "exchange_server_url",            # e.g. "mail.klinikum-bs.de" (EWS host)
    "exchange_auth_mode",             # "service_account" | "modern_auth" | "basic"
    "exchange_service_account_user",  # service-account UPN (service_account mode)
    "exchange_tenant_id",             # Entra tenant (modern_auth mode)
    "exchange_mcp_external_enabled",  # expose Exchange MCP to external LLM clients
    # Meeting → MS Planner: target plan for mirrored action items (empty = off)
    "meeting_planner_plan_id",
    # Meeting moderator: AI-Account the moderator agent uses (empty = first available)
    "meeting_moderator_ai_account_id",
    # Meeting → generate a real decision doc + slide deck from the result (default off)
    "meeting_artifact_enabled",
    # "Dreaming": periodic adaptive user-profile refresh (default off)
    "dreaming_enabled",
}


def _get_fernet() -> Fernet | None:
    """Get Fernet cipher from the encryption key, or None if not configured."""
    key = settings.encryption_key
    if not key:
        return None
    # Pad or hash key to 32 bytes for Fernet
    key_bytes = key.encode()[:32].ljust(32, b"\0")
    return Fernet(base64.urlsafe_b64encode(key_bytes))


class SettingsService:
    """Persists platform settings to the database with optional encryption."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self._fernet = _get_fernet()

    def _encrypt(self, value: str) -> str:
        if self._fernet and value:
            return self._fernet.encrypt(value.encode()).decode()
        return value

    def _decrypt(self, value: str, key: str) -> str:
        if self._fernet and value and key in SECRET_KEYS:
            try:
                return self._fernet.decrypt(value.encode()).decode()
            except (InvalidToken, Exception):
                logger.warning(f"Could not decrypt setting '{key}' - returning empty")
                return ""
        return value

    async def get(self, key: str) -> str | None:
        result = await self.db.execute(
            select(PlatformSettings).where(PlatformSettings.key == key)
        )
        row = result.scalar_one_or_none()
        if not row:
            return None
        return self._decrypt(row.value, key)

    async def set(self, key: str, value: str) -> None:
        if key not in ALLOWED_KEYS:
            raise ValueError(f"Unknown setting: {key}")

        is_secret = key in SECRET_KEYS
        stored_value = self._encrypt(value) if is_secret else value

        result = await self.db.execute(
            select(PlatformSettings).where(PlatformSettings.key == key)
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.value = stored_value
            existing.is_secret = is_secret
        else:
            self.db.add(PlatformSettings(
                key=key, value=stored_value, is_secret=is_secret
            ))

    async def get_all(self) -> dict[str, str]:
        result = await self.db.execute(select(PlatformSettings))
        rows = result.scalars().all()
        return {row.key: self._decrypt(row.value, row.key) for row in rows}

    async def load_into_config(self) -> None:
        """Load all DB settings into the in-memory config singleton."""
        db_settings = await self.get_all()
        if not db_settings:
            logger.info("No persisted settings found - using env defaults")
            return

        loaded = 0
        for key, value in db_settings.items():
            if not value:
                continue
            if hasattr(settings, key):
                current = getattr(settings, key)
                # Convert types
                if isinstance(current, bool):
                    value = value.lower() in ("true", "1", "yes")
                elif isinstance(current, int):
                    value = int(value)
                setattr(settings, key, value)
                loaded += 1

        if loaded:
            logger.info(f"Loaded {loaded} settings from database")
