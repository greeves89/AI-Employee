"""Interne MCP-Server zulassen — pro Eintrag, nicht für die ganze Installation.

Der Anlass: ein Administrator trägt seinen EIGENEN MCP-Server ein, der im Haus
steht (``skbs-s-kichat.klinikum-bs.de`` → 192.168.245.87), und bekommt eine
Ablehnung mit dem Hinweis auf eine Umgebungsvariable. Die gibt es zwar, aber sie
öffnet die **ganze** Installation und braucht einen Neustart — für einen einzigen
Eintrag ist das das falsche Werkzeug.

Der Haken ist deshalb bewusst eng: er erlaubt **private** Adressen, und sonst
nichts. Was nie ein MCP-Server ist, bleibt gesperrt, egal wer klickt:

* **Link-local**, darunter der Cloud-Metadatenpunkt 169.254.169.254 — der Klassiker
  jeder SSRF-Kette
* **Multicast, reserviert, unbestimmt**
* **Loopback** — innerhalb des Containers ist das dieser Server selbst. Ein
  interner MCP-Server steht dort nicht; die eigene API schon.

Alle betroffenen Endpunkte sind ohnehin ``require_admin``; der Haken hält fest,
dass die interne Adresse Absicht war und kein Vertipper.
"""

import unittest
from ipaddress import ip_address

from app.api.mcp_servers import _forbidden_ip_reason, _validate_mcp_url, McpDiscoveryError


class NeverAllowedTests(unittest.TestCase):
    """Diese Adressen bleiben gesperrt — auch mit gesetztem Haken."""

    CASES = {
        "cloud-metadata": "169.254.169.254",
        "link-local": "169.254.1.1",
        "multicast": "224.0.0.1",
        "unbestimmt": "0.0.0.0",
        "loopback": "127.0.0.1",
        "loopback-v6": "::1",
    }

    def test_they_are_refused_without_the_flag(self):
        for label, ip in self.CASES.items():
            with self.subTest(label):
                self.assertIsNotNone(_forbidden_ip_reason(ip_address(ip)))

    def test_the_flag_does_not_open_them(self):
        """Der eigentliche Punkt: „trotzdem hinzufügen" ist kein Generalschlüssel."""
        for label, ip in self.CASES.items():
            with self.subTest(label):
                self.assertIsNotNone(
                    _forbidden_ip_reason(ip_address(ip), allow_private=True),
                    f"{label} ({ip}) darf der Haken nicht freigeben",
                )

    def test_the_metadata_endpoint_is_named_in_the_message(self):
        reason = _forbidden_ip_reason(ip_address("169.254.169.254"), allow_private=True)
        self.assertIn("169.254.169.254", reason)


class PrivateHostTests(unittest.TestCase):
    """Genau der Fall aus der Meldung."""

    PRIVATE = ("192.168.245.87", "10.1.2.3", "172.16.0.9")

    def test_private_is_refused_by_default(self):
        for ip in self.PRIVATE:
            with self.subTest(ip):
                self.assertIsNotNone(_forbidden_ip_reason(ip_address(ip)))

    def test_the_flag_allows_it(self):
        for ip in self.PRIVATE:
            with self.subTest(ip):
                self.assertIsNone(_forbidden_ip_reason(ip_address(ip), allow_private=True))

    def test_the_message_points_at_the_checkbox_not_at_an_env_var(self):
        """Wer die Meldung liest, soll wissen, was er KLICKEN kann — nicht, welche
        Variable er in einer Datei setzen und danach neu starten muss."""
        reason = _forbidden_ip_reason(ip_address("192.168.245.87"))
        self.assertIn("192.168.245.87", reason)
        self.assertNotIn("MCP_ALLOW_PRIVATE_URLS", reason)

    def test_a_public_address_never_needed_the_flag(self):
        self.assertIsNone(_forbidden_ip_reason(ip_address("93.184.216.34")))


class UrlLiteralTests(unittest.TestCase):
    """``_validate_mcp_url`` prüft IP-Literale, bevor überhaupt aufgelöst wird."""

    def test_a_private_literal_is_refused_by_default(self):
        with self.assertRaises(McpDiscoveryError):
            _validate_mcp_url("https://192.168.245.87/mcp")

    def test_a_private_literal_passes_with_the_flag(self):
        got = _validate_mcp_url("https://192.168.245.87/mcp", allow_private=True)
        self.assertTrue(got.startswith("https://192.168.245.87"))

    def test_loopback_literal_stays_refused_with_the_flag(self):
        with self.assertRaises(McpDiscoveryError):
            _validate_mcp_url("http://127.0.0.1:8080/mcp", allow_private=True)

    def test_metadata_literal_stays_refused_with_the_flag(self):
        with self.assertRaises(McpDiscoveryError):
            _validate_mcp_url("http://169.254.169.254/mcp", allow_private=True)

    def test_the_flag_changes_nothing_about_the_scheme(self):
        """Kein Schlupfloch nebenbei: file://, ftp:// und Anmeldedaten in der URL
        bleiben abgelehnt."""
        for bad in ("file:///etc/passwd", "ftp://example.test/mcp",
                    "https://user:pw@example.test/mcp", "not-a-url"):
            with self.subTest(bad):
                with self.assertRaises(McpDiscoveryError):
                    _validate_mcp_url(bad, allow_private=True)


class PersistenceTests(unittest.TestCase):
    """Der Haken muss die Zeile überleben — sonst schlägt die nächste
    Aktualisierung der Werkzeuge wieder fehl."""

    def test_the_model_carries_the_flag(self):
        from app.models.mcp_server import McpServer

        self.assertTrue(hasattr(McpServer, "allow_private_host"))

    def test_refresh_passes_the_stored_flag(self):
        from pathlib import Path

        src = (Path(__file__).resolve().parents[1] / "app/api/mcp_servers.py").read_text()
        block = src.split("async def refresh_mcp_tools")[1].split("\n@router")[0]
        self.assertIn("allow_private=bool(server.allow_private_host)", block)

    def test_the_column_is_ensured_at_startup(self):
        from pathlib import Path

        src = (Path(__file__).resolve().parents[1] / "app/main.py").read_text()
        self.assertIn("allow_private_host boolean NOT NULL DEFAULT false", src)


if __name__ == "__main__":
    unittest.main()
