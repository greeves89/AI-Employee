"""Kiosk nur vom Gerät selbst (Plan Sicherheit 2026-11, Punkt 1).

Der Kiosk ist ohne Anmeldung (lokale Anzeige auf dem Pi) und nur mit KIOSK_ENABLED
eingehängt. Caddy sperrte bisher nur den Cloudflare-Tunnel — aus dem LAN antwortete
er mit 200 (am 14.11.2026 vom Mac aus gemessen). Jetzt antwortet er dort mit 404.
Die Regel wurde in einem Wegwerf-Caddy auf dem Pi geprüft: localhost 200,
LAN-Adresse 404, anderes Gerät 404.
"""

import re
import unittest
from pathlib import Path

CADDY = (Path(__file__).resolve().parents[2] / "Caddyfile").read_text(encoding="utf-8")


class KioskNurLokal(unittest.TestCase):
    def test_matcher_nur_loopback_und_docker(self):
        m = re.search(r"@nichtlokal not remote_ip ([^\n]+)", CADDY)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1).split(), ["127.0.0.1/8", "::1", "172.16.0.0/12"])

    def test_beide_kiosk_pfade_gesperrt(self):
        for pfad in ("/api/v1/kiosk*", "/kiosk*"):
            block = CADDY.split(f"handle {pfad} {{", 1)[1].split("reverse_proxy", 1)[0]
            self.assertIn('respond @tunnel "Not found" 404', block, pfad)
            self.assertIn('respond @nichtlokal "Not found" 404', block, pfad)


if __name__ == "__main__":
    unittest.main()
