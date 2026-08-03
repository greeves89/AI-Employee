"""Test that get_provider_scopes respects settings overrides."""

from unittest.mock import MagicMock, patch

from app.core.oauth_providers import get_provider, get_provider_scopes


def _mock_settings(**kwargs):
    m = MagicMock()
    for k, v in kwargs.items():
        setattr(m, k, v)
    return m


def test_default_scopes_when_unset():
    provider = get_provider("google")
    with patch("app.config.settings", _mock_settings(oauth_google_scopes="")):
        scopes = get_provider_scopes(provider)
    assert scopes == list(provider.scopes)


def test_override_replaces_defaults():
    provider = get_provider("microsoft")
    override = "openid,email,Mail.Read"
    with patch("app.config.settings", _mock_settings(oauth_microsoft_scopes=override)):
        scopes = get_provider_scopes(provider)
    assert scopes == ["openid", "email", "Mail.Read"]
    assert "Mail.Send" not in scopes


def test_override_strips_whitespace():
    provider = get_provider("google")
    with patch(
        "app.config.settings",
        _mock_settings(oauth_google_scopes="openid, email , profile"),
    ):
        scopes = get_provider_scopes(provider)
    assert scopes == ["openid", "email", "profile"]


def test_whitespace_only_override_falls_back():
    provider = get_provider("github")
    with patch("app.config.settings", _mock_settings(oauth_github_scopes="   ")):
        scopes = get_provider_scopes(provider)
    assert scopes == list(provider.scopes)


def test_empty_override_falls_back():
    provider = get_provider("anthropic")
    with patch("app.config.settings", _mock_settings(oauth_anthropic_scopes="")):
        scopes = get_provider_scopes(provider)
    assert scopes == list(provider.scopes)
