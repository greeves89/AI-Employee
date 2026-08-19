"""Test that get_provider_scopes respects settings overrides."""

from unittest.mock import MagicMock, patch

from app.core.oauth_providers import get_provider, get_provider_scopes, microsoft_scopes


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


def test_microsoft_scopes_fordern_transkript_scopes_an():
    # Regression PR #581: die Scopes standen in PROVIDERS["microsoft"].scopes,
    # aber nicht in MICROSOFT_OPTIONAL_SCOPES — microsoft_scopes() filterte sie
    # heraus und der echte Consent-Dialog hat sie nie angefordert (Tools -> 403).
    with patch("app.config.settings", _mock_settings(oauth_microsoft_scopes="")):
        scopes = microsoft_scopes()
    assert "OnlineMeetings.Read" in scopes
    assert "OnlineMeetingTranscript.Read.All" in scopes


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


def test_no_raw_provider_scopes_in_oauth_service():
    """Every scope read in oauth_service must go through get_provider_scopes so an
    override is honored on ALL paths — including the recorded-granted-scope
    fallback in persist_tokens (issue #419 point 3). A bare `provider.scopes`
    would silently record/request the full hardcoded set, re-widening scopes.
    """
    import ast
    import inspect

    from app.services import oauth_service

    src = inspect.getsource(oauth_service)
    tree = ast.parse(src)
    raw = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr == "scopes"
        and isinstance(node.value, ast.Name)
        and node.value.id == "provider"
    ]
    assert not raw, (
        "oauth_service still reads provider.scopes directly; route it through "
        "get_provider_scopes(provider) so scope overrides apply everywhere"
    )
