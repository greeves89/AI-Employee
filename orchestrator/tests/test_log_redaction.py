"""Regression tests for container-log secret redaction (self-improvement flow).

These logs are shown to agents, so a leak here hands a credential to an
autonomous process. Fail-closed: we assert secrets vanish and only benign text
survives verbatim.
"""

import unittest

from app.core.log_redaction import redact_logs


class LogRedactionTests(unittest.TestCase):
    def _assert_gone(self, text: str, *secrets: str):
        out = redact_logs(text)
        for s in secrets:
            self.assertNotIn(s, out, f"secret leaked: {s!r} in {out!r}")

    def test_bearer_token_redacted(self):
        self._assert_gone("Authorization: Bearer abcDEF1234567890xyz", "abcDEF1234567890xyz")

    def test_jwt_redacted(self):
        jwt = "eyJhbGciOi.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2QT4"
        self._assert_gone(f"ticket={jwt}", jwt)

    def test_provider_api_keys_redacted(self):
        self._assert_gone("OPENAI_API_KEY=sk-proj-ABCDEFGH12345678ABCDEFGH", "sk-proj-ABCDEFGH12345678ABCDEFGH")
        self._assert_gone("token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345", "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345")
        self._assert_gone("AWS AKIAIOSFODNN7EXAMPLE key", "AKIAIOSFODNN7EXAMPLE")

    def test_sensitive_key_value_pairs_redacted(self):
        for line, secret in [
            ("ENCRYPTION_KEY=supersecretvalue1234", "supersecretvalue1234"),
            ("AGENT_TOKEN=deadbeefcafebabe0123", "deadbeefcafebabe0123"),
            ('{"password": "hunter2hunter2"}', "hunter2hunter2"),
            ("DATABASE_URL=postgres://u:p@db:5432/app", "postgres://u:p@db:5432/app"),
        ]:
            self._assert_gone(line, secret)

    def test_private_key_block_redacted(self):
        block = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----"
        self._assert_gone(f"key:\n{block}", "MIIEowIBAAKCAQEA")

    def test_benign_lines_survive(self):
        line = "INFO 172.19.0.4 - GET /api/v1/tasks 200 OK in 12ms"
        self.assertEqual(redact_logs(line), line)

    def test_empty_input(self):
        self.assertEqual(redact_logs(""), "")


if __name__ == "__main__":
    unittest.main()
