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
    sso_only_login: bool | None = None
    require_user_approval: bool | None = None
    revoke_msgraph_on_logout: bool | None = None
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
    # Improvement engine thresholds
    improvement_suggestion_model: str | None = None
    improvement_min_ratings: int | None = None
    improvement_suggestion_threshold: float | None = None
    improvement_min_skill_usages: int | None = None
    improvement_skill_threshold: float | None = None
    improvement_analysis_interval: int | None = None
    # Voice live-session config
    voice_stt_provider: str | None = None       # "faster_whisper" | "openai_whisper"
    voice_tts_provider: str | None = None       # "edge_tts" | "elevenlabs"
    voice_tts_voice: str | None = None          # voice id (e.g. "de-DE-KatjaNeural")
    voice_llm_model: str | None = None          # e.g. "claude-haiku-4-5-20251001"
    voice_language: str | None = None           # ISO code or None for auto-detect
    voice_openai_api_key: str | None = None     # optional, secret
    voice_elevenlabs_api_key: str | None = None # optional, secret
    voice_azure_speech_key: str | None = None   # Azure Speech (STT/TTS), secret
    voice_azure_speech_region: str | None = None # e.g. "germanywestcentral"
    # Realtime voice front (Nova Sonic / AWS Bedrock / Azure) — platform default.
    voice_interaction_model: str | None = None      # "" = classic pipeline; else engine e.g. "nova_sonic"
    voice_interaction_account_id: str | None = None  # AI-account id providing the realtime creds
    # On-prem Exchange (EWS) connection config — auth is per-user via impersonation
    exchange_server_url: str | None = None               # EWS host, e.g. "mail.klinikum-bs.de"
    exchange_auth_mode: str | None = None                # "service_account" | "modern_auth" | "basic"
    exchange_service_account_user: str | None = None     # service-account UPN
    exchange_service_account_password: str | None = None # secret
    exchange_tenant_id: str | None = None                # Entra tenant (modern_auth)
    exchange_mcp_external_enabled: bool | None = None
    # SMTP relay for SENDING mail — the universal send path (works where EWS is blocked).
    smtp_relay_host: str | None = None                    # relay host/IP, e.g. "192.168.20.213"
    smtp_relay_port: str | None = None                    # default 25
    smtp_relay_starttls: bool | None = None               # use STARTTLS if offered (default true)
    smtp_relay_user: str | None = None                    # optional; empty = anonymous relay
    smtp_relay_password: str | None = None                # optional secret
    smtp_allowed_recipient_domains: str | None = None     # CSV; empty = sender's own domain only; "*" = any
    # Meeting → MS Planner: target plan ID for mirrored action items (empty = off)
    meeting_planner_plan_id: str | None = None
    # Meeting moderator: which AI-Account the moderator agent uses (empty = first available)
    meeting_moderator_ai_account_id: str | None = None
    # "Dreaming": periodic adaptive user-profile refresh from memories
    dreaming_enabled: bool | None = None
    # Reflection ("Nachtschicht"): nightly out-of-band transcript reflection
    reflection_enabled: str | None = None       # "true" | "false"
    reflection_hour: int | str | None = None    # local hour 0-23
    reflection_mode: str | None = None          # auto | hybrid | strict
    reflection_token_budget: int | str | None = None


class VoiceSettings(BaseModel):
    stt_provider: str = "faster_whisper"
    tts_provider: str = "edge_tts"
    tts_voice: str = "de-DE-KatjaNeural"
    llm_model: str = "claude-haiku-4-5-20251001"
    language: str | None = None
    available_stt: list[str] = []
    available_tts: list[str] = []
    available_llm: list[str] = []
    available_voices: list[dict] = []
    has_openai_key: bool = False
    has_elevenlabs_key: bool = False
    has_azure_speech_key: bool = False
    azure_speech_region: str = ""
    # Realtime voice front (platform default): "" = classic pipeline, else engine.
    voice_interaction_model: str = ""
    voice_interaction_account_id: str = ""


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
    sso_only_login: bool = False
    require_user_approval: bool = False
    revoke_msgraph_on_logout: bool = False
    # Provider
    model_provider: str
    has_bedrock: bool
    has_vertex: bool
    has_foundry: bool
    has_codex_oauth: bool = False
    aws_region: str
    vertex_region: str
    foundry_resource: str
    # OAuth integrations
    has_google_oauth: bool = False
    has_microsoft_oauth: bool = False
    has_apple_oauth: bool = False
    msgraph_mcp_external_enabled: bool = False
    # Lifecycle
    agent_idle_timeout_minutes: int = 30
    # Improvement engine thresholds
    improvement_suggestion_model: str = "claude-haiku-4-5-20251001"
    improvement_min_ratings: int = 5
    improvement_suggestion_threshold: float = 3.5
    improvement_min_skill_usages: int = 5
    improvement_skill_threshold: float = 3.0
    improvement_analysis_interval: int = 3600
    # Meeting → MS Planner + "Dreaming"-Memory
    meeting_planner_plan_id: str = ""
    meeting_moderator_ai_account_id: str = ""
    dreaming_enabled: bool = False
