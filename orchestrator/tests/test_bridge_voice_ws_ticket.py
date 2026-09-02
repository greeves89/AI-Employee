"""Der Voice-WebSocket der Bridge darf kein Dauer-JWT in die URL schreiben.

Query-Parameter landen in Proxy- und Zugriffsprotokollen, im Verlauf und im
Referer. Das Web-Frontend wurde dafuer mit #337 auf Einmal-Tickets umgestellt
und hat bewusst KEINEN Rueckfall auf ``token=``. Die Bridge blieb dabei
uebrig — sie war der letzte Erzeuger der Warnung "WebSocket using legacy
token= param" im Plattformlog (#636). Geprueft werden die ECHTEN Funktionen.
"""

import sys
import unittest
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "computer-use-bridge"))
import tray_app  # noqa: E402

JWT = "eyJhbGciOiJIUzI1NiJ9.ZGVtbw.c2lnbmF0dXJl"


class VoiceWsUrlTests(unittest.TestCase):
    def test_url_carries_ticket_not_token(self):
        url = tray_app._voice_ws_url("https://example.invalid", "agent-1", "tkt123")
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.assertEqual(query.get("ticket"), ["tkt123"])
        self.assertNotIn("token", query)

    def test_jwt_never_reaches_the_url(self):
        """Gegenprobe gegen den Rueckfall: selbst wenn jemand das JWT
        durchreicht, darf es nicht als ``token=`` interpretiert werden."""
        url = tray_app._voice_ws_url("https://example.invalid", "agent-1", JWT)
        self.assertNotIn("token=", url)

    def test_scheme_is_upgraded_to_wss(self):
        url = tray_app._voice_ws_url("https://example.invalid/", "agent-1", "tkt123")
        self.assertTrue(url.startswith("wss://example.invalid/api/v1/ws/agents/agent-1/voice?"))


class WsTicketTests(unittest.TestCase):
    def setUp(self):
        self._orig = tray_app._api
        self.calls = []

        def fake_api(method, base_url, path, token, body=None):
            self.calls.append((method, base_url, path, token, body))
            return {"ticket": "tkt-from-server"}

        tray_app._api = fake_api

    def tearDown(self):
        tray_app._api = self._orig

    def test_ticket_is_fetched_with_the_jwt_in_the_header(self):
        ticket = tray_app.api_ws_ticket("https://example.invalid", JWT)
        self.assertEqual(ticket, "tkt-from-server")
        method, base_url, path, token, _body = self.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(path, "/api/v1/ws/ticket")
        self.assertEqual(token, JWT)

    def test_failure_propagates_instead_of_falling_back(self):
        """Kein stiller Rueckfall auf ``token=``: ein Ticketfehler ist ein
        Verbindungsfehler und muss sichtbar bleiben."""

        def boom(*_a, **_kw):
            raise OSError("server sagt 503")

        tray_app._api = boom
        with self.assertRaises(OSError):
            tray_app.api_ws_ticket("https://example.invalid", JWT)


if __name__ == "__main__":
    unittest.main()
