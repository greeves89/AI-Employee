"""Regression tests for container-log secret redaction (self-improvement flow).

These logs are shown to agents, so a leak here hands a credential to an
autonomous process. Fail-closed: we assert secrets vanish and only benign text
survives verbatim.
"""

import unittest

from app.core.log_redaction import redact_logs, scrub_log


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

    def test_telegram_bot_token_redacted(self):
        # python-telegram-bot's InvalidToken embeds the token in prose.
        token = "123456789:AAHdqTcvbd1234567890ABCDEFGHIJKLMNOP"
        self._assert_gone(f"The token {token} was rejected by the server", token)
        self._assert_gone(
            f"[Telegram] Failed to start bot for Bob: The token `{token}` was rejected", token
        )

    def test_private_key_block_redacted(self):
        block = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----"
        self._assert_gone(f"key:\n{block}", "MIIEowIBAAKCAQEA")

    def test_benign_lines_survive(self):
        line = "INFO 172.19.0.4 - GET /api/v1/tasks 200 OK in 12ms"
        self.assertEqual(redact_logs(line), line)

    def test_empty_input(self):
        self.assertEqual(redact_logs(""), "")


class ScrubLogTests(unittest.TestCase):
    """CWE-117: user-controlled values must not be able to forge log records."""

    def test_newlines_removed(self):
        forged = "agent-1\nINFO fake admin login succeeded"
        out = scrub_log(forged)
        self.assertNotIn("\n", out)
        self.assertEqual(out, "agent-1INFO fake admin login succeeded")

    def test_carriage_return_removed(self):
        out = scrub_log("sess\r\nDELETE /everything")
        self.assertNotIn("\r", out)
        self.assertNotIn("\n", out)

    def test_other_control_chars_removed(self):
        # NUL and an ANSI escape must not survive into a log line.
        out = scrub_log("id\x00\x1b[31mred")
        self.assertNotIn("\x00", out)
        self.assertNotIn("\x1b", out)
        self.assertEqual(out, "id[31mred")

    def test_unicode_line_separators_removed(self):
        # U+2028/U+2029 are treated as line breaks by some JSON/JS log viewers
        # and must not survive to forge a new log line.
        out = scrub_log("sess DELETE /all")
        self.assertNotIn(" ", out)
        self.assertNotIn(" ", out)
        self.assertEqual(out, "sessDELETE/all")

    def test_tab_and_plain_text_survive(self):
        self.assertEqual(scrub_log("proj\tname-42"), "proj\tname-42")

    def test_non_string_coerced(self):
        self.assertEqual(scrub_log(1234), "1234")
        self.assertEqual(scrub_log(None), "None")


if __name__ == "__main__":
    unittest.main()
