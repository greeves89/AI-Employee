"""Unit tests for the pure MCP OAuth client helpers (#426)."""
import base64
import hashlib
import time
from urllib.parse import parse_qs, urlparse

import pytest

from app.services import mcp_oauth_client as oc


# --- PKCE -----------------------------------------------------------------

def test_gen_pkce_verifier_length_in_rfc_range():
    verifier, challenge = oc.gen_pkce()
    assert 43 <= len(verifier) <= 128
    assert challenge  # non-empty


def test_gen_pkce_challenge_is_s256_of_verifier():
    verifier, challenge = oc.gen_pkce()
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    assert challenge == expected


def test_gen_pkce_is_random():
    assert oc.gen_pkce()[0] != oc.gen_pkce()[0]


# --- WWW-Authenticate parsing ---------------------------------------------

def test_parse_www_authenticate_extracts_params():
    hdr = 'Bearer error="invalid_token", resource_metadata="https://h/.well-known/oauth-protected-resource/srv"'
    params = oc.parse_www_authenticate(hdr)
    assert params["error"] == "invalid_token"
    assert params["resource_metadata"] == "https://h/.well-known/oauth-protected-resource/srv"


def test_parse_www_authenticate_non_bearer_returns_empty():
    assert oc.parse_www_authenticate('Basic realm="x"') == {}


def test_parse_www_authenticate_none():
    assert oc.parse_www_authenticate(None) == {}


def test_resource_metadata_url_prefers_challenge():
    hdr = 'Bearer resource_metadata="https://h/.well-known/oauth-protected-resource/s"'
    assert oc.resource_metadata_url(hdr) == "https://h/.well-known/oauth-protected-resource/s"


def test_resource_metadata_url_falls_back_to_well_known():
    url = oc.resource_metadata_url(None, "https://host.example/api/v1/mcp/srv")
    assert url == "https://host.example/.well-known/oauth-protected-resource/api/v1/mcp/srv"


def test_resource_metadata_url_none_when_no_hint():
    assert oc.resource_metadata_url(None, None) is None


# --- Metadata selection ---------------------------------------------------

def test_pick_authorization_server_first():
    assert oc.pick_authorization_server({"authorization_servers": ["https://as1", "https://as2"]}) == "https://as1"


def test_pick_authorization_server_missing():
    assert oc.pick_authorization_server({}) is None
    assert oc.pick_authorization_server(None) is None


def test_as_metadata_urls_covers_rfc8414_and_openid():
    urls = oc.as_metadata_urls("https://as.example/tenant")
    assert "https://as.example/.well-known/oauth-authorization-server/tenant" in urls
    assert "https://as.example/tenant/.well-known/oauth-authorization-server" in urls
    assert any("openid-configuration" in u for u in urls)
    assert len(urls) == len(set(urls))  # de-duped


def test_as_metadata_urls_invalid_issuer():
    assert oc.as_metadata_urls("not-a-url") == []


def test_select_endpoints():
    meta = {
        "issuer": "https://as",
        "authorization_endpoint": "https://as/authorize",
        "token_endpoint": "https://as/token",
        "registration_endpoint": "https://as/register",
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "scopes_supported": ["MCP.Access"],
    }
    got = oc.select_endpoints(meta)
    assert got["token_endpoint"] == "https://as/token"
    assert got["registration_endpoint"] == "https://as/register"
    assert got["scopes_supported"] == ["MCP.Access"]


def test_select_endpoints_empty():
    got = oc.select_endpoints(None)
    assert got["token_endpoint"] is None
    assert got["grant_types_supported"] == []


def test_default_scope_prefers_prm():
    assert oc.default_scope({"scopes_supported": ["A", "B"]}, {"scopes_supported": ["C"]}) == "A B"


def test_default_scope_falls_back_to_as():
    assert oc.default_scope({}, {"scopes_supported": ["C"]}) == "C"


def test_default_scope_empty():
    assert oc.default_scope({}, {}) == ""


# --- Request builders -----------------------------------------------------

def test_build_registration_request_public_pkce():
    body = oc.build_registration_request(redirect_uri="https://o/cb", client_name="AI-Employee")
    assert body["redirect_uris"] == ["https://o/cb"]
    assert body["token_endpoint_auth_method"] == "none"
    assert "refresh_token" in body["grant_types"]


def test_build_authorization_url_has_pkce_and_state():
    url = oc.build_authorization_url(
        authorization_endpoint="https://as/authorize",
        client_id="cid",
        redirect_uri="https://o/cb",
        state="st8",
        code_challenge="chal",
        scope="MCP.Access",
        resource="https://h/api/v1/mcp/srv",
    )
    q = parse_qs(urlparse(url).query)
    assert q["response_type"] == ["code"]
    assert q["code_challenge_method"] == ["S256"]
    assert q["code_challenge"] == ["chal"]
    assert q["state"] == ["st8"]
    assert q["scope"] == ["MCP.Access"]
    assert q["resource"] == ["https://h/api/v1/mcp/srv"]
    assert q["client_id"] == ["cid"]


def test_build_authorization_url_appends_when_query_present():
    url = oc.build_authorization_url(
        authorization_endpoint="https://as/authorize?foo=bar",
        client_id="cid", redirect_uri="https://o/cb", state="s", code_challenge="c",
    )
    assert "?foo=bar&" in url


def test_build_authorization_url_omits_empty_scope_and_resource():
    url = oc.build_authorization_url(
        authorization_endpoint="https://as/a", client_id="c", redirect_uri="https://o/cb",
        state="s", code_challenge="c",
    )
    q = parse_qs(urlparse(url).query)
    assert "scope" not in q
    assert "resource" not in q


def test_build_token_exchange_data():
    data = oc.build_token_exchange_data(
        code="AC", code_verifier="V", client_id="cid", redirect_uri="https://o/cb",
        resource="https://h/mcp",
    )
    assert data["grant_type"] == "authorization_code"
    assert data["code_verifier"] == "V"
    assert data["resource"] == "https://h/mcp"
    assert "client_secret" not in data


def test_build_token_exchange_data_confidential():
    data = oc.build_token_exchange_data(
        code="AC", code_verifier="V", client_id="cid", redirect_uri="https://o/cb",
        client_secret="sek",
    )
    assert data["client_secret"] == "sek"


def test_build_refresh_data():
    data = oc.build_refresh_data(refresh_token="RT", client_id="cid", scope="s", resource="https://h/mcp")
    assert data["grant_type"] == "refresh_token"
    assert data["refresh_token"] == "RT"
    assert data["scope"] == "s"
    assert data["resource"] == "https://h/mcp"


# --- Token response -------------------------------------------------------

def test_parse_token_response_full():
    out = oc.parse_token_response({
        "access_token": "AT", "refresh_token": "RT", "expires_in": 900, "scope": "s",
    })
    assert out["access_token"] == "AT"
    assert out["refresh_token"] == "RT"
    assert out["scope"] == "s"
    assert out["expires_at"] is not None
    assert out["expires_at"] > time.time()


def test_parse_token_response_no_rotation():
    out = oc.parse_token_response({"access_token": "AT", "expires_in": 60})
    assert out["refresh_token"] is None


def test_parse_token_response_no_expiry():
    out = oc.parse_token_response({"access_token": "AT"})
    assert out["expires_at"] is None


def test_parse_token_response_missing_access_raises():
    with pytest.raises(ValueError):
        oc.parse_token_response({"token_type": "Bearer"})


# --- Expiry ---------------------------------------------------------------

def test_is_expired_none_is_true():
    assert oc.is_expired(None) is True


def test_is_expired_future_false():
    assert oc.is_expired(time.time() + 3600) is False


def test_is_expired_within_skew_true():
    assert oc.is_expired(time.time() + 10, skew_seconds=60) is True


def test_is_expired_past_true():
    assert oc.is_expired(time.time() - 5) is True
