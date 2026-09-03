"""Interne MCP-Server zulassen — pro Eintrag, nicht für die ganze Installation.

Der Anlass: ein Administrator trägt seinen EIGENEN MCP-Server ein, der im Haus
steht (``ki-chat.example.com`` → 192.168.245.87), und bekommt eine
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
from unittest.mock import AsyncMock, patch

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


class DiscoveryPathTests(unittest.IsolatedAsyncioTestCase):
    """Der Haken muss den ganzen Weg durchhalten — nicht nur den ersten Waechter.

    Genau hier ist er beim ersten Anlauf steckengeblieben: ``_validate_mcp_url``
    bekam ihn, der DNS-aufloesende ``_assert_discovery_host_allowed`` nicht. Damit
    wirkte er nur bei IP-Adressen in der URL — und ausgerechnet nicht bei einem
    NAMEN, der sich auf eine private Adresse aufloest. Das ist aber der gedachte
    Fall: ``mcp.intern.example`` oder ein Docker-Containername.

    Aufgefallen ist es erst beim Ausprobieren gegen einen echten Server. Diese
    Tests gehen deshalb durch ``_discover_tools`` statt durch die Waechter direkt.
    """

    URL = "http://mcp-im-haus:8000/mcp"

    def _resolving_to(self, ip):
        from ipaddress import ip_address
        return patch("app.api.mcp_servers._resolve_host_ips",
                     new=AsyncMock(return_value=[ip_address(ip)]))

    async def test_a_name_resolving_to_a_private_address_is_refused_by_default(self):
        from app.api.mcp_servers import _discover_tools, McpDiscoveryError

        with self._resolving_to("192.168.245.87"):
            with self.assertRaises(McpDiscoveryError) as cm:
                await _discover_tools(self.URL)
        self.assertIn("private", str(cm.exception).lower())

    async def test_the_flag_gets_it_past_the_resolving_guard(self):
        """Es darf hier NICHT mehr am Waechter scheitern. Dass danach keine
        Verbindung zustande kommt, ist in Ordnung — nur die Ablehnung wegen der
        Adresse darf nicht mehr kommen."""
        from app.api.mcp_servers import _discover_tools, McpDiscoveryError

        with self._resolving_to("172.18.0.13"):
            try:
                await _discover_tools(self.URL, allow_private=True)
            except McpDiscoveryError as e:
                self.assertNotIn("private address", str(e).lower(),
                                 "Der Haken kam beim aufloesenden Waechter nicht an")
            except Exception:
                pass  # Transportfehler ist erwartet — es gibt keinen Server

    async def test_the_flag_does_not_open_loopback_on_this_path_either(self):
        from app.api.mcp_servers import _discover_tools, McpDiscoveryError

        with self._resolving_to("127.0.0.1"):
            with self.assertRaises(McpDiscoveryError) as cm:
                await _discover_tools(self.URL, allow_private=True)
        self.assertIn("loopback", str(cm.exception).lower())

    async def test_the_flag_does_not_open_the_metadata_endpoint_either(self):
        from app.api.mcp_servers import _discover_tools, McpDiscoveryError

        with self._resolving_to("169.254.169.254"):
            with self.assertRaises(McpDiscoveryError):
                await _discover_tools(self.URL, allow_private=True)


class GuardWiringTests(unittest.TestCase):
    """Kein Waechter darf den Haken unterwegs verlieren."""

    @staticmethod
    def _src():
        from pathlib import Path
        return (Path(__file__).resolve().parents[1] / "app/api/mcp_servers.py").read_text()

    def test_discovery_passes_the_flag_to_both_guards(self):
        block = self._src().split("async def _discover_tools")[1].split("\nasync def ")[0]
        self.assertIn("_validate_mcp_url(url, allow_private=allow_private)", block)
        self.assertIn("_assert_discovery_host_allowed(safe_url, allow_private=allow_private)", block)

    def test_calling_a_tool_passes_it_too(self):
        """Wer eingetragen werden durfte, muss auch aufrufbar sein — sonst laesst
        sich ein interner Server hinzufuegen, aber nicht ausprobieren."""
        block = self._src().split("async def _call_tool")[1].split("\nasync def ")[0]
        self.assertIn("allow_private=allow_private", block)


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
