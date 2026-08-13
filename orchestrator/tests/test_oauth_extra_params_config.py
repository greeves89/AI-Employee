"""Regression test for #571: Microsoft OAuth unconditionally sent prompt=consent,
which tenants with end-user consent disabled can never satisfy — even after an
admin grants tenant-wide consent, Entra re-forces the dialog on every attempt.

get_provider_extra_params() now merges an opt-in oauth_microsoft_prompt setting
into the static auth_extra_params instead of Microsoft's config hardcoding
"prompt": "consent". Default (unset) means no prompt param is sent at all.
"""

from unittest.mock import MagicMock, patch

from app.core.oauth_providers import get_provider, get_provider_extra_params


def _mock_settings(**kwargs):
    m = MagicMock()
    for k, v in kwargs.items():
        setattr(m, k, v)
    return m


def test_microsoft_no_prompt_by_default():
    provider = get_provider("microsoft")
    with patch("app.config.settings", _mock_settings(oauth_microsoft_prompt="")):
        params = get_provider_extra_params(provider)
    assert "prompt" not in params


def test_microsoft_prompt_opt_in():
    provider = get_provider("microsoft")
    with patch("app.config.settings", _mock_settings(oauth_microsoft_prompt="consent")):
        params = get_provider_extra_params(provider)
    assert params["prompt"] == "consent"


def test_microsoft_prompt_whitespace_only_is_unset():
    provider = get_provider("microsoft")
    with patch("app.config.settings", _mock_settings(oauth_microsoft_prompt="   ")):
        params = get_provider_extra_params(provider)
    assert "prompt" not in params


def test_google_extra_params_unaffected():
    """Google keeps access_type=offline + prompt=consent — out of scope for #571."""
    provider = get_provider("google")
    with patch("app.config.settings", _mock_settings(oauth_microsoft_prompt="")):
        params = get_provider_extra_params(provider)
    assert params == {"access_type": "offline", "prompt": "consent"}


def test_oauth_service_uses_get_provider_extra_params():
    """generate_auth_url must merge extra params through get_provider_extra_params,
    not read provider.auth_extra_params directly — otherwise the opt-in setting
    would be silently bypassed for the standard (non-Anthropic) auth flow."""
    import ast
    import inspect

    from app.services import oauth_service

    src = inspect.getsource(oauth_service)
    tree = ast.parse(src)
    raw = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr == "auth_extra_params"
        and isinstance(node.value, ast.Name)
        and node.value.id == "provider"
    ]
    assert not raw, (
        "oauth_service still reads provider.auth_extra_params directly; route it "
        "through get_provider_extra_params(provider) so the prompt override applies"
    )
