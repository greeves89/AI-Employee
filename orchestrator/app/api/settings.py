from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.dependencies import require_admin, require_auth
from app.models.oauth_integration import OAuthIntegration, OAuthProvider
from app.schemas.settings import SettingsResponse, SettingsUpdate, VoiceSettings
from app.services.settings_service import SettingsService
from app.services.voice_providers import get_active_voice_config
from app.services.voice_providers.tts_edge import EDGE_VOICES

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
    codex_result = await db.execute(
        select(OAuthIntegration).where(OAuthIntegration.provider == OAuthProvider.CODEX)
    )
    codex_integration = codex_result.scalar_one_or_none()

    if has_api_key:
        auth_method = "api_key"
    elif has_oauth_token:
        auth_method = "oauth_token"
    else:
        auth_method = "none"

    svc = SettingsService(db)

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
        sso_only_login=settings.sso_only_login,
        require_user_approval=settings.require_user_approval,
        revoke_msgraph_on_logout=settings.revoke_msgraph_on_logout,
        # Provider info
        model_provider=settings.model_provider,
        has_bedrock=bool(settings.aws_access_key_id and settings.aws_secret_access_key),
        has_vertex=bool(settings.vertex_project_id and settings.vertex_credentials_json),
        has_foundry=bool(settings.foundry_api_key and settings.foundry_resource),
        has_codex_oauth=codex_integration is not None,
        aws_region=settings.aws_region,
        vertex_region=settings.vertex_region,
        foundry_resource=settings.foundry_resource,
        # OAuth integrations
        has_google_oauth=bool(settings.oauth_google_client_id),
        has_microsoft_oauth=bool(settings.oauth_microsoft_client_id),
        has_apple_oauth=bool(settings.oauth_apple_client_id),
        msgraph_mcp_external_enabled=(await svc.get("msgraph_mcp_external_enabled") or "false").lower() in ("true", "1", "yes"),
        # Lifecycle
        agent_idle_timeout_minutes=int(await svc.get("agent_idle_timeout_minutes") or "30"),
        # Improvement engine thresholds
        improvement_suggestion_model=await svc.get("improvement_suggestion_model") or "claude-haiku-4-5-20251001",
        improvement_min_ratings=int(await svc.get("improvement_min_ratings") or "5"),
        improvement_suggestion_threshold=float(await svc.get("improvement_suggestion_threshold") or "3.5"),
        improvement_min_skill_usages=int(await svc.get("improvement_min_skill_usages") or "5"),
        improvement_skill_threshold=float(await svc.get("improvement_skill_threshold") or "3.0"),
        improvement_analysis_interval=int(await svc.get("improvement_analysis_interval") or "3600"),
    )


# Mapping from SettingsUpdate field names to config attribute names
_FIELD_MAP: dict[str, str] = {
    "model_provider": "model_provider",
    "default_model": "default_model",
    "max_turns": "max_turns",
    "max_agents": "max_agents",
    "registration_open": "registration_open",
    "sso_only_login": "sso_only_login",
    "require_user_approval": "require_user_approval",
    "revoke_msgraph_on_logout": "revoke_msgraph_on_logout",
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

    # Improvement engine thresholds
    _IMPROVEMENT_FIELDS = {
        "improvement_suggestion_model": str,
        "improvement_min_ratings": int,
        "improvement_suggestion_threshold": float,
        "improvement_min_skill_usages": int,
        "improvement_skill_threshold": float,
        "improvement_analysis_interval": int,
    }
    for field_name, field_type in _IMPROVEMENT_FIELDS.items():
        value = getattr(data, field_name, None)
        if value is not None:
            await svc.set(field_name, str(value))

    # Voice provider config
    _VOICE_FIELDS = [
        "voice_stt_provider", "voice_tts_provider", "voice_tts_voice",
        "voice_llm_model", "voice_language",
        "voice_openai_api_key", "voice_elevenlabs_api_key",
    ]
    for field_name in _VOICE_FIELDS:
        value = getattr(data, field_name, None)
        if value is not None:
            await svc.set(field_name, str(value))

    # On-prem Exchange (EWS) connection config — stored DB-only (read by the MCP
    # transport via SettingsService); not mirrored into the config singleton.
    _EXCHANGE_FIELDS = [
        "exchange_server_url", "exchange_auth_mode",
        "exchange_service_account_user", "exchange_service_account_password",
        "exchange_tenant_id",
    ]
    for field_name in _EXCHANGE_FIELDS:
        value = getattr(data, field_name, None)
        if value is not None:
            await svc.set(field_name, str(value))
    if data.exchange_mcp_external_enabled is not None:
        await svc.set("exchange_mcp_external_enabled",
                      "true" if data.exchange_mcp_external_enabled else "false")

    await db.commit()
    return {"status": "updated"}


@router.get("/voice", response_model=VoiceSettings)
async def get_voice_settings(
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Return active voice provider config + available choices for the admin UI."""
    cfg = await get_active_voice_config(db)
    svc = SettingsService(db)
    # Currently only edge_tts exposes a curated voice list synchronously;
    # ElevenLabs voices are fetched on-demand by the frontend when that
    # provider is selected (requires an API key call).
    voices = EDGE_VOICES if cfg["tts_provider"] == "edge_tts" else []
    return VoiceSettings(
        stt_provider=cfg["stt_provider"],
        tts_provider=cfg["tts_provider"],
        tts_voice=cfg["tts_voice"],
        llm_model=cfg["llm_model"],
        language=cfg["language"],
        available_stt=cfg["available_stt"],
        available_tts=cfg["available_tts"],
        available_llm=cfg["available_llm"],
        available_voices=voices,
        has_openai_key=cfg["has_openai_key"],
        has_elevenlabs_key=cfg["has_elevenlabs_key"],
    )


@router.get("/agent-mounts")
async def get_agent_mount_catalog(
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Return the mount catalog FILTERED by the caller's per-user grants.

    Admin sees the full catalog with their server-side modes (the source-of-truth).
    Non-admin sees only labels granted via user_mount_access, with the
    grant's `mode` (which may be downgraded to "ro" from the catalog's "rw").
    """
    from app.core.mounts import get_effective_catalog
    from app.models.user import UserRole
    from app.models.user_mount_access import UserMountAccess
    from sqlalchemy import select

    catalog = await get_effective_catalog(db)

    if hasattr(user, "role") and user.role == UserRole.ADMIN:
        # Admin: full catalog
        return {
            "mounts": [
                {"label": e.label, "container_path": e.container_path, "mode": e.mode}
                for e in catalog.values()
            ]
        }

    # Non-admin: a label is available if granted per-user (user_mount_access) OR
    # via the user's group/role (custom_role.permissions.mount_labels). The two
    # are a UNION — group grant + manual per-user grant.
    from app.core.permissions import get_effective_permissions
    perms = await get_effective_permissions(user, db)
    role_mount_labels = set(perms.get("mount_labels") or [])

    grants = (await db.execute(
        select(UserMountAccess).where(UserMountAccess.user_id == user.id)
    )).scalars().all()
    grant_by_label = {g.mount_label: g.mode for g in grants}

    out = []
    for label, entry in catalog.items():
        user_mode = grant_by_label.get(label)
        role_granted = label in role_mount_labels
        if user_mode is None and not role_granted:
            continue
        # Per-user grant mode is stricter-capped against the catalog; a pure group
        # grant uses the catalog's default mode for the mount/brain.
        if user_mode is not None:
            eff_mode = "ro" if "ro" in (entry.mode, user_mode) else "rw"
        else:
            eff_mode = entry.mode
        out.append({"label": label, "container_path": entry.container_path, "mode": eff_mode})
    return {"mounts": out}


# ── Admin endpoints: per-user mount access management ──

@router.get("/agent-mounts/access/{user_id}")
async def list_user_mount_access(
    user_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Admin: list mount grants for a specific user."""
    from app.models.user import UserRole
    from app.models.user_mount_access import UserMountAccess
    from sqlalchemy import select

    if not (hasattr(user, "role") and user.role == UserRole.ADMIN):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Admin only")

    grants = (await db.execute(
        select(UserMountAccess).where(UserMountAccess.user_id == user_id)
    )).scalars().all()
    return {"grants": [{"mount_label": g.mount_label, "mode": g.mode} for g in grants]}


@router.put("/agent-mounts/access/{user_id}")
async def set_user_mount_access(
    user_id: str,
    body: dict,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Admin: replace the user's mount grants atomically.

    Body: {"grants": [{"mount_label": "downloads", "mode": "rw"}, ...]}
    Existing grants not in the list are removed.
    """
    from app.models.user import UserRole
    from app.models.user_mount_access import UserMountAccess
    from app.core.mounts import parse_mount_catalog
    from sqlalchemy import delete as sql_delete
    from fastapi import HTTPException

    if not (hasattr(user, "role") and user.role == UserRole.ADMIN):
        raise HTTPException(status_code=403, detail="Admin only")

    new_grants = body.get("grants", [])
    if not isinstance(new_grants, list):
        raise HTTPException(status_code=422, detail="grants must be a list")

    from app.core.mounts import get_effective_catalog
    catalog = await get_effective_catalog(db)
    valid_labels = set(catalog.keys())

    # Validate
    cleaned = []
    for g in new_grants:
        label = (g.get("mount_label") or "").strip()
        mode = (g.get("mode") or "").strip()
        if label not in valid_labels:
            raise HTTPException(status_code=422, detail=f"unknown mount_label: {label}")
        if mode not in ("ro", "rw"):
            raise HTTPException(status_code=422, detail=f"invalid mode: {mode} (must be ro|rw)")
        cleaned.append((label, mode))

    # Atomic replace
    await db.execute(sql_delete(UserMountAccess).where(UserMountAccess.user_id == user_id))
    for label, mode in cleaned:
        db.add(UserMountAccess(user_id=user_id, mount_label=label, mode=mode))
    await db.commit()
    return {"user_id": user_id, "grants": [{"mount_label": l, "mode": m} for l, m in cleaned]}


# ── Idle-Stop settings (admin-global + per-agent capped) ──

@router.get("/idle-stop")
async def get_idle_stop_max(
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Return the global max-idle-minutes (admin-set). 0/null = disabled."""
    from app.models.platform_settings import PlatformSettings
    ps = await db.get(PlatformSettings, "max_idle_minutes")
    try:
        val = int(ps.value) if ps and ps.value else 0
    except Exception:
        val = 0
    return {"max_idle_minutes": val}


@router.put("/idle-stop")
async def set_idle_stop_max(
    body: dict,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Admin: set the global max-idle-minutes (0 to disable)."""
    from app.models.user import UserRole
    from app.models.platform_settings import PlatformSettings
    from fastapi import HTTPException

    if not (hasattr(user, "role") and user.role == UserRole.ADMIN):
        raise HTTPException(status_code=403, detail="Admin only")

    try:
        minutes = int(body.get("max_idle_minutes", 0))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="max_idle_minutes must be an integer")
    if minutes < 0 or minutes > 60 * 24 * 7:  # 1 week cap
        raise HTTPException(status_code=422, detail="max_idle_minutes must be in [0, 10080]")

    ps = await db.get(PlatformSettings, "max_idle_minutes")
    if ps:
        ps.value = str(minutes)
    else:
        db.add(PlatformSettings(key="max_idle_minutes", value=str(minutes)))
    await db.commit()
    return {"max_idle_minutes": minutes}


@router.put("/msgraph-mcp-external")
async def set_msgraph_mcp_external(
    body: dict,
    user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Admin: expose the MS Graph MCP server to external LLM clients (OpenWebUI).

    Refuses to enable without a configured Microsoft app registration, so the
    toggle can never be switched on into a dead end. Takes effect immediately
    (live ``settings`` update) and persists across restarts.
    """
    from fastapi import HTTPException

    enable = bool(body.get("enabled", False))
    if enable and not settings.oauth_microsoft_client_id:
        raise HTTPException(
            status_code=422,
            detail="Configure the Microsoft app registration (OAUTH_MICROSOFT_CLIENT_ID) first.",
        )
    svc = SettingsService(db)
    await svc.set("msgraph_mcp_external_enabled", "true" if enable else "false")
    await db.commit()  # SettingsService.set() does not commit; persist explicitly
    settings.msgraph_mcp_external_enabled = enable  # live effect, no restart needed
    return {"msgraph_mcp_external_enabled": enable}
