"""SSO (OIDC) provider configuration for user login.

Separate from oauth_providers.py which handles external integrations (Gmail, Drive, etc.).
SSO providers use the same OAuth client credentials but with minimal OIDC scopes.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SSOProviderConfig:
    """Configuration for an OIDC SSO provider."""

    name: str  # "google", "microsoft"
    display_name: str  # "Google", "Microsoft"
    icon: str  # For frontend (SVG name or lucide icon)
    authorization_url: str
    token_url: str
    userinfo_url: str
    jwks_uri: str  # For ID token verification
    issuer: str  # Expected issuer in ID token
    scopes: list[str] = field(default_factory=lambda: ["openid", "email", "profile"])
    auth_extra_params: dict[str, str] = field(default_factory=dict)
    # Settings keys (reuses same keys as integration OAuth)
    client_id_setting: str = ""
    client_secret_setting: str = ""


SSO_PROVIDERS: dict[str, SSOProviderConfig] = {
    "google": SSOProviderConfig(
        name="google",
        display_name="Google",
        icon="google",
        authorization_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        userinfo_url="https://www.googleapis.com/oauth2/v3/userinfo",
        jwks_uri="https://www.googleapis.com/oauth2/v3/certs",
        issuer="https://accounts.google.com",
        scopes=["openid", "email", "profile"],
        auth_extra_params={"access_type": "online", "prompt": "select_account"},
        client_id_setting="oauth_google_client_id",
        client_secret_setting="oauth_google_client_secret",
    ),
    "microsoft": SSOProviderConfig(
        name="microsoft",
        display_name="Microsoft",
        icon="microsoft",
        authorization_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
        userinfo_url="https://graph.microsoft.com/v1.0/me",
        jwks_uri="https://login.microsoftonline.com/common/discovery/v2.0/keys",
        issuer="https://login.microsoftonline.com/{tenant}/v2.0",
        scopes=["openid", "email", "profile"],
        client_id_setting="oauth_microsoft_client_id",
        client_secret_setting="oauth_microsoft_client_secret",
    ),
}


def get_sso_provider(name: str) -> SSOProviderConfig:
    """Get SSO provider config by name."""
    if name not in SSO_PROVIDERS:
        raise KeyError(f"Unknown SSO provider: {name}. Available: {list(SSO_PROVIDERS.keys())}")
    return SSO_PROVIDERS[name]


def get_sso_client_id(provider: SSOProviderConfig) -> str:
    from app.config import settings
    return getattr(settings, provider.client_id_setting, "")


def get_sso_client_secret(provider: SSOProviderConfig) -> str:
    from app.config import settings
    return getattr(settings, provider.client_secret_setting, "")


def is_sso_available(provider: SSOProviderConfig) -> bool:
    """Check if an SSO provider has client credentials configured."""
    return bool(get_sso_client_id(provider) and get_sso_client_secret(provider))
