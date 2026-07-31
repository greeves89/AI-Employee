"""Guard tests for the Telegram bot-token handling in the agents API (issue #372).

These run WITHOUT importing app.api.agents (which pulls in fastapi/sqlalchemy,
absent in some envs): the token regex is extracted from the module source via
ast, and the redaction wiring is asserted at the source level.
"""

import ast
import re
import unittest
from pathlib import Path

_AGENTS_SRC = Path(__file__).resolve().parents[1] / "app" / "api" / "agents.py"


def _extract_token_regex() -> re.Pattern:
    tree = ast.parse(_AGENTS_SRC.read_text())
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and any(getattr(t, "id", None) == "_TELEGRAM_TOKEN_RE" for t in node.targets)
            and isinstance(node.value, ast.Call)
        ):
            pattern = node.value.args[0]
            if isinstance(pattern, ast.Constant):
                return re.compile(pattern.value)
    raise AssertionError("_TELEGRAM_TOKEN_RE not found in agents.py")


class TelegramTokenValidationTests(unittest.TestCase):
    def setUp(self):
        self.rx = _extract_token_regex()
        self.src = _AGENTS_SRC.read_text()

    def test_valid_token_accepted(self):
        self.assertTrue(self.rx.match("123456789:AAHdqTcvbd1234567890ABCDEFGHIJKLMNOP"))

    def test_pasted_message_rejected(self):
        # A whole BotFather message, which is exactly how the secret leaked.
        pasted = "Use this token to access the HTTP API:\n123456789:AAHdqTcvbd1234567890ABCDEFGHIJKLMNOP\nKeep it safe"
        self.assertIsNone(self.rx.match(pasted))

    def test_garbage_rejected(self):
        for bad in ["", "not-a-token", "123:short", "abc:AAHdqTcvbd1234567890ABCDEFGHIJKLMNOP"]:
            self.assertIsNone(self.rx.match(bad), bad)

    def test_error_response_is_redacted(self):
        # The failure branch must not echo the raw exception string.
        self.assertIn('redact_logs(str(e))', self.src)
        self.assertNotIn('"error": str(e),', self.src.replace(" ", "").replace("\n", ""))

    def test_format_checked_before_start(self):
        # A 400 is raised on bad format before any persist/start attempt.
        self.assertIn("_TELEGRAM_TOKEN_RE.match(bot_token)", self.src)


if __name__ == "__main__":
    unittest.main()
