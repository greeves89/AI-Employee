"""Die Push-Einstellungen muessen im Behaelter ankommen.

Gefunden am 2026-09-03: Die APNs-Werte waren in der Konfiguration vorgesehen und
im Code ausgewertet — aber in KEINER Compose-Datei durchgereicht. Wer sie in die
.env eintrug, bei dem kamen sie trotzdem nicht an; der Versand blieb still aus,
und nichts wies auf die Ursache hin. Der Dienst meldete lediglich "nicht
konfiguriert", was nach einem fehlenden Schluessel aussieht statt nach einer
fehlenden Zeile in der Compose-Datei.

Der Test leitet die erwarteten Namen aus der Konfiguration ab statt sie
aufzuzaehlen: Kommt spaeter ein apns-Feld dazu, faellt das Durchreichen hier auf,
ohne dass jemand daran denken muss.
"""

import re
import unittest
from pathlib import Path

from app.config import Settings

REPO = Path(__file__).resolve().parents[2]
COMPOSE = [
    "docker-compose.yml",
    "docker-compose.aiemployee.yml",
    "docker-compose.community.yml",
]


def _erwartete_namen() -> set[str]:
    return {
        name.upper()
        for name in Settings.model_fields
        if name.startswith("apns_")
    }


class PushEinstellungenTests(unittest.TestCase):
    def test_es_gibt_ueberhaupt_apns_felder(self):
        """Sonst pruefte alles Weitere die leere Menge."""
        self.assertTrue(_erwartete_namen(), "keine apns_-Felder in der Konfiguration")

    def test_jede_compose_datei_reicht_sie_durch(self):
        erwartet = _erwartete_namen()
        for datei in COMPOSE:
            pfad = REPO / datei
            with self.subTest(datei=datei):
                self.assertTrue(pfad.exists(), f"{datei} fehlt")
                text = pfad.read_text(encoding="utf-8")
                fehlend = {n for n in erwartet if not re.search(rf"^\s+{n}:", text, re.M)}
                self.assertEqual(
                    set(), fehlend,
                    f"{datei} reicht nicht durch: {sorted(fehlend)} — "
                    "in der .env gesetzte Werte kaemen im Behaelter nicht an",
                )

    def test_schluessel_werte_haben_keinen_ersatzwert(self):
        """Ein Ersatzwert fuer Geheimnisse wuerde echte Werte still verdecken."""
        for datei in COMPOSE:
            text = (REPO / datei).read_text(encoding="utf-8")
            for name in ("APNS_AUTH_KEY", "APNS_KEY_ID", "APNS_TEAM_ID"):
                treffer = re.search(rf"^\s+{name}:\s*(.+)$", text, re.M)
                with self.subTest(datei=datei, name=name):
                    self.assertIsNotNone(treffer)
                    self.assertRegex(treffer.group(1).strip(), rf"^\$\{{{name}:-\}}$")


if __name__ == "__main__":
    unittest.main()
