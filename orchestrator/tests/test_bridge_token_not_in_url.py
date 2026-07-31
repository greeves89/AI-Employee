"""Regression guard: the Computer-Use bridge must not put its JWT in the URL.

Issue #373 — the token was sent as a `?token=` query parameter, which uvicorn's
access log and the client log recorded in full. The token now travels only in the
Authorization header. This source-level check keeps it from creeping back (the
bridge has no CI test suite of its own; this runs in the orchestrator pytest job).
"""

import unittest
from pathlib import Path

_BRIDGE_SRC = Path(__file__).resolve().parents[2] / "computer-use-bridge" / "bridge.py"


class BridgeTokenNotInUrlTests(unittest.TestCase):
    def setUp(self):
        self.src = _BRIDGE_SRC.read_text()

    def test_token_not_in_query_string(self):
        # The urlencode building the WS URL must carry session_id only.
        self.assertIn('urlencode({"session_id": self.session_id})', self.src)
        self.assertNotIn('"token": self.token', self.src)

    def test_token_still_sent_as_auth_header(self):
        self.assertIn('"Authorization": f"Bearer {self.token}"', self.src)

    def test_raw_url_with_query_not_logged(self):
        # The old leak was `log.info(f"Connecting to {url}")` where url held the
        # query string. Logging the bare path is fine; logging {url} is not.
        self.assertNotIn('log.info(f"Connecting to {url}")', self.src)


if __name__ == "__main__":
    unittest.main()
