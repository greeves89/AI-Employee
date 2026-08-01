"""Guards for the Computer-Use bridge's configurable proxy headers (issue #374).

An identity-aware proxy (Cloudflare Access, Google IAP, oauth2-proxy, Authelia,
Teleport) authenticates non-browser clients with request headers. The bridge must
be able to send those on the WebSocket handshake, from three sources (env
shortcut / config file / --header flag), without ever logging their values and
without letting them shadow the Authorization bearer token.

The bridge has no CI suite of its own, so this runs in the orchestrator pytest
job. It combines a source-level guard (so the wiring can't silently regress) with
a live import of the pure-stdlib header-collection helpers.
"""

import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

_BRIDGE_SRC_PATH = Path(__file__).resolve().parents[2] / "computer-use-bridge" / "bridge.py"


def _load_bridge_module():
    """Import bridge.py, stubbing the optional ``websockets`` dependency."""
    if "websockets" not in sys.modules:
        try:
            import websockets  # noqa: F401
        except ImportError:
            sys.modules["websockets"] = types.ModuleType("websockets")
    spec = importlib.util.spec_from_file_location("bridge_under_test", _BRIDGE_SRC_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BridgeExtraHeadersSourceGuard(unittest.TestCase):
    def setUp(self):
        self.src = _BRIDGE_SRC_PATH.read_text()

    def test_extra_headers_merged_into_handshake(self):
        # The extra headers must be spread into the handshake dict.
        self.assertIn("**self.extra_headers", self.src)

    def test_authorization_cannot_be_shadowed(self):
        # Authorization must be set AFTER the spread so it always wins.
        self.assertIn(
            '{**self.extra_headers, "Authorization": f"Bearer {self.token}"}',
            self.src,
        )

    def test_header_cli_flag_exists(self):
        self.assertIn('"--header"', self.src)

    def test_config_file_and_cf_shortcuts_supported(self):
        self.assertIn("extra_headers", self.src)
        self.assertIn("CF_ACCESS_CLIENT_ID", self.src)
        self.assertIn("CF_ACCESS_CLIENT_SECRET", self.src)
        self.assertIn("CF-Access-Client-Id", self.src)
        self.assertIn("CF-Access-Client-Secret", self.src)

    def test_header_values_never_logged(self):
        # We log the header NAMES (join over the keys) but never the dict itself,
        # because the values are service-token credentials.
        self.assertIn("', '.join(sorted(self.extra_headers))", self.src)
        self.assertNotIn("log.info(f\"Extra request headers: {self.extra_headers}", self.src)


class BridgeExtraHeadersBehaviour(unittest.TestCase):
    def setUp(self):
        self.m = _load_bridge_module()
        self._saved_env = {
            k: os.environ.get(k)
            for k in ("CF_ACCESS_CLIENT_ID", "CF_ACCESS_CLIENT_SECRET")
        }
        for k in self._saved_env:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_parse_header_arg(self):
        self.assertEqual(
            self.m._parse_header_arg("CF-Access-Client-Id: abc"),
            ("CF-Access-Client-Id", "abc"),
        )
        # A colon in the value is preserved (only the first splits).
        self.assertEqual(self.m._parse_header_arg("X: a:b"), ("X", "a:b"))
        with self.assertRaises(ValueError):
            self.m._parse_header_arg("no-colon")
        with self.assertRaises(ValueError):
            self.m._parse_header_arg(": empty-name")

    def test_precedence_env_config_cli(self):
        os.environ["CF_ACCESS_CLIENT_ID"] = "env-id"
        os.environ["CF_ACCESS_CLIENT_SECRET"] = "env-secret"
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(
                {"extra_headers": {"CF-Access-Client-Secret": "cfg-secret", "X-Cfg": "1"}},
                f,
            )
            cfg_path = f.name
        self.addCleanup(os.unlink, cfg_path)
        self.m.BRIDGE_CONFIG_PATH = cfg_path
        headers = self.m.collect_extra_headers(["CF-Access-Client-Id: cli-id", "X-Extra: y"])
        self.assertEqual(headers["CF-Access-Client-Id"], "cli-id")     # CLI > env
        self.assertEqual(headers["CF-Access-Client-Secret"], "cfg-secret")  # config > env
        self.assertEqual(headers["X-Cfg"], "1")
        self.assertEqual(headers["X-Extra"], "y")

    def test_missing_config_file_is_ignored(self):
        self.m.BRIDGE_CONFIG_PATH = "/nonexistent/path/does/not/exist.json"
        self.assertEqual(self.m.collect_extra_headers(None), {})

    def test_bridge_merge_authorization_wins(self):
        bridge = self.m.Bridge(
            "wss://x", "TOK", "sess",
            extra_headers={"Authorization": "evil", "CF-Access-Client-Id": "z"},
        )
        merged = {**bridge.extra_headers, "Authorization": f"Bearer {bridge.token}"}
        self.assertEqual(merged["Authorization"], "Bearer TOK")
        self.assertEqual(merged["CF-Access-Client-Id"], "z")


if __name__ == "__main__":
    unittest.main()
