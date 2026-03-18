"""OAuth provider registry - declarative config for each provider.

To add a new provider, add an entry to PROVIDERS dict and set the
corresponding OAUTH_<NAME>_CLIENT_ID and OAUTH_<NAME>_CLIENT_SECRET
environment variables.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class OAuthProviderConfig:
    """Configuration for an OAuth 2.0 provider."""

    name: str  # "google", "microsoft", "apple"
    display_name: str  # "Google", "Microsoft", "Apple"
    icon: str  # Lucide icon name for frontend
    description: str  # Short description for UI card
    authorization_url: str
    token_url: str
    userinfo_url: str | None  # To fetch account_label (email)
    scopes: list[str] = field(default_factory=list)
    supports_refresh: bool = True
    # Extra params for authorization URL (e.g., access_type=offline for Google)
    auth_extra_params: dict[str, str] = field(default_factory=dict)
    # Apple uses JWT-based client secret
    token_exchange_method: str = "standard"  # "standard" or "apple_jwt"
    # Settings keys for client credentials
    client_id_setting: str = ""
    client_secret_setting: str = ""


PROVIDERS: dict[str, OAuthProviderConfig] = {
    "google": OAuthProviderConfig(
        name="google",
        display_name="Google",
        icon="Mail",
        description="Gmail, Google Drive, Calendar",
        authorization_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        userinfo_url="https://www.googleapis.com/oauth2/v2/userinfo",
        scopes=[
            "openid",
            "email",
            "profile",
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/calendar",
            "https://www.googleapis.com/auth/drive",
        ],
        auth_extra_params={"access_type": "offline", "prompt": "consent"},
        client_id_setting="oauth_google_client_id",
        client_secret_setting="oauth_google_client_secret",
    ),
    "microsoft": OAuthProviderConfig(
        name="microsoft",
        display_name="Microsoft",
        icon="Cloud",
        description="Outlook, OneDrive, Teams",
        authorization_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
        userinfo_url="https://graph.microsoft.com/v1.0/me",
        scopes=[
            "openid",
            "email",
            "profile",
            "offline_access",
            "Mail.ReadWrite",
            "Calendars.ReadWrite",
            "Files.ReadWrite",
        ],
        client_id_setting="oauth_microsoft_client_id",
        client_secret_setting="oauth_microsoft_client_secret",
    ),
    "github": OAuthProviderConfig(
        name="github",
        display_name="GitHub",
        icon="Github",
        description="Repositories, Pull Requests, Issues",
        authorization_url="https://github.com/login/oauth/authorize",
        token_url="https://github.com/login/oauth/access_token",
        userinfo_url="https://api.github.com/user",
        scopes=["repo", "workflow", "read:org", "read:user", "user:email"],
        supports_refresh=False,
        token_exchange_method="pat_or_oauth",
        client_id_setting="oauth_github_client_id",
        client_secret_setting="oauth_github_client_secret",
    ),
    "anthropic": OAuthProviderConfig(
        name="anthropic",
        display_name="Anthropic (Claude)",
        icon="Bot",
        description="Claude Code OAuth — eigene Bot-Session",
        authorization_url="https://platform.claude.com/oauth/authorize",
        token_url="https://platform.claude.com/v1/oauth/token",
        userinfo_url=None,
        scopes=["user:inference", "user:profile"],
        supports_refresh=True,
        auth_extra_params={"response_type": "code"},
        token_exchange_method="anthropic_oauth",  # JSON body, no client_secret
        client_id_setting="oauth_anthropic_client_id",
        client_secret_setting="",  # Public client — no secret needed
    ),
    "apple": OAuthProviderConfig(
        name="apple",
        display_name="Apple",
        icon="Smartphone",
        description="Apple ID, iCloud",
        authorization_url="https://appleid.apple.com/auth/authorize",
        token_url="https://appleid.apple.com/auth/token",
        userinfo_url=None,  # Apple returns user info only in the initial callback
        scopes=["name", "email"],
        auth_extra_params={"response_mode": "form_post"},
        token_exchange_method="apple_jwt",
        client_id_setting="oauth_apple_client_id",
        client_secret_setting="oauth_apple_team_id",  # Apple uses team_id + key_id
    ),
}


def get_provider(name: str) -> OAuthProviderConfig:
    """Get provider config by name. Raises KeyError if not found."""
    if name not in PROVIDERS:
        raise KeyError(f"Unknown OAuth provider: {name}. Available: {list(PROVIDERS.keys())}")
    return PROVIDERS[name]


def get_provider_client_id(provider: OAuthProviderConfig) -> str:
    """Get the client_id for a provider from settings."""
    from app.config import settings
    return getattr(settings, provider.client_id_setting, "")


def get_provider_client_secret(provider: OAuthProviderConfig) -> str:
    """Get the client_secret for a provider from settings."""
    from app.config import settings
    return getattr(settings, provider.client_secret_setting, "")


def is_provider_available(provider: OAuthProviderConfig) -> bool:
    """Check if a provider has client credentials configured."""
    return bool(get_provider_client_id(provider))
