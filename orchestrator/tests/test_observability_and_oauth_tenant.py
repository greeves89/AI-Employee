"""Tests for the configurable Microsoft tenant authority, the unified SSO Graph
scopes, and the Langfuse observability no-op behaviour."""

import unittest

from app.config import settings
from app.core.oauth_providers import apply_tenant
from app.core.sso_providers import SSO_PROVIDERS
from app.services.observability_service import observability


class TenantAuthorityTests(unittest.TestCase):
    def setUp(self):
        self._orig = settings.oauth_microsoft_tenant_id

    def tearDown(self):
        settings.oauth_microsoft_tenant_id = self._orig

    def test_specific_tenant_replaces_common(self):
        settings.oauth_microsoft_tenant_id = "3a8d7242-4618-4af3-8e1f-036328554c98"
        url = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
        out = apply_tenant(url)
        self.assertIn("/3a8d7242-4618-4af3-8e1f-036328554c98/", out)
        self.assertNotIn("/common/", out)

    def test_common_is_noop(self):
        settings.oauth_microsoft_tenant_id = "common"
        url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
        self.assertEqual(apply_tenant(url), url)

    def test_non_microsoft_url_untouched(self):
        settings.oauth_microsoft_tenant_id = "some-tenant"
        url = "https://accounts.google.com/o/oauth2/v2/auth"
        self.assertEqual(apply_tenant(url), url)


class SsoScopeTests(unittest.TestCase):
    def test_microsoft_login_requests_graph_and_offline(self):
        scopes = SSO_PROVIDERS["microsoft"].scopes
        self.assertIn("offline_access", scopes)
        self.assertIn("Mail.Send", scopes)

    def test_google_login_stays_minimal(self):
        self.assertNotIn("Mail.Send", SSO_PROVIDERS["google"].scopes)


class ObservabilityNoOpTests(unittest.TestCase):
    def test_disabled_without_keys(self):
        # Test env has no Langfuse keys → tracing must be a no-op.
        self.assertFalse(observability.enabled)

    def test_trace_url_none_without_public_url(self):
        orig = settings.langfuse_public_url
        settings.langfuse_public_url = ""
        try:
            self.assertIsNone(observability.trace_url("abc"))
        finally:
            settings.langfuse_public_url = orig

    def test_trace_url_built_with_public_url(self):
        orig = settings.langfuse_public_url
        settings.langfuse_public_url = "https://lf.example.com/"
        try:
            url = observability.trace_url("task123")
            self.assertIsNotNone(url)
            self.assertIn("/traces/task123", url)
        finally:
            settings.langfuse_public_url = orig


if __name__ == "__main__":
    unittest.main()
