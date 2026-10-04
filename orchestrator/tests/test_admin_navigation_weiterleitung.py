"""#899: Admin-Konsole mit einer Ebene, ``/settings`` nur eigene Einstellungen.

Geprueft wird das Verhalten des Navigationsmodells ``frontend/src/lib/admin-bereiche.ts``,
indem Node die Datei wirklich ausfuehrt (Typen werden nur abgestreift):

* Alte Verweise — Lesezeichen, Handbuch, Links aus Benachrichtigungen — landen
  im richtigen Bereich der Admin-Konsole.
* Ein Mitglied wird von ``/settings`` nie in die Admin-Konsole geschickt.
* Jeder Admin-Bereich steht genau einmal in der Navigation (eine Ebene).
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

MODUL = Path(__file__).resolve().parents[2] / "frontend/src/lib/admin-bereiche.ts"
NODE = shutil.which("node")


def _ausfuehren(ausdruck: str):
    skript = (
        f"import * as m from {json.dumps(MODUL.as_uri())};\n"
        f"process.stdout.write(JSON.stringify({ausdruck}));\n"
    )
    ergebnis = subprocess.run(
        [NODE, "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", skript],
        capture_output=True, text=True, timeout=30,
    )
    if ergebnis.returncode != 0:
        raise AssertionError(ergebnis.stderr)
    return json.loads(ergebnis.stdout)


@unittest.skipUnless(NODE, "Node.js nicht installiert")
class AlteEinstellungsVerweise(unittest.TestCase):
    def weiter(self, tab, anker, admin):
        return _ausfuehren(
            f"m.alteEinstellungenWeiterleitung({json.dumps(tab)}, {json.dumps(anker)}, {json.dumps(admin)})")

    def test_lizenz_eintragen_fuehrt_direkt_zur_lizenz(self):
        self.assertEqual(self.weiter("system", "#lizenz", True), "/admin?tab=lizenz")
        self.assertEqual(self.weiter(None, "#lizenz", True), "/admin?tab=lizenz")

    def test_anlagenweite_reiter_ziehen_in_die_konsole(self):
        self.assertEqual(self.weiter("system", "", True), "/admin?tab=system")
        self.assertEqual(self.weiter("voice", "", True), "/admin?tab=sprache")
        self.assertEqual(self.weiter("modelle", "", True), "/admin?tab=modelle")
        self.assertEqual(self.weiter("integrationen", "", True), "/admin?tab=integrationen-anlage")

    def test_eigene_bereiche_bleiben_auf_der_seite(self):
        self.assertIsNone(self.weiter("meine", "", True))
        self.assertIsNone(self.weiter(None, "", True))

    def test_mitglied_wird_nie_in_die_konsole_geschickt(self):
        for tab in ("system", "voice", "modelle", "integrationen", "meine", None):
            for anker in ("", "#lizenz"):
                with self.subTest(tab=tab, anker=anker):
                    self.assertIsNone(self.weiter(tab, anker, False))

    def test_unbekannter_reiter_leitet_nicht_um(self):
        self.assertIsNone(self.weiter("gibt-es-nicht", "", True))


@unittest.skipUnless(NODE, "Node.js nicht installiert")
class AdminReiterAusDerAdresse(unittest.TestCase):
    def bereich(self, tab):
        return _ausfuehren(f"m.adminBereichAusTab({json.dumps(tab)})")

    def test_jeder_bereich_ist_direkt_ansteuerbar(self):
        alle = _ausfuehren("m.ADMIN_GRUPPEN.flatMap((g) => g.bereiche)")
        for bereich in alle:
            with self.subTest(bereich=bereich):
                self.assertEqual(self.bereich(bereich)["bereich"], bereich)

    def test_alte_reiter_der_konsole_bleiben_gueltig(self):
        """Links aus Benachrichtigungen und Handbuch nutzen diese Kennungen."""
        for alt in ("users", "roles", "ai-accounts", "secrets", "health", "audit",
                    "budget", "feedback", "dlp", "master-rules", "gesetze", "pages",
                    "sso-groups", "agents", "assignments", "second-brains", "web-search"):
            with self.subTest(alt=alt):
                self.assertEqual(self.bereich(alt)["bereich"], alt)

    def test_frueherer_sammelreiter_einstellungen(self):
        self.assertEqual(self.bereich("settings")["bereich"], "modelle")

    def test_lizenz_oeffnet_system_und_lizenz(self):
        self.assertEqual(self.bereich("lizenz"), {"bereich": "system", "anker": "lizenz"})

    def test_kaputter_oder_fehlender_reiter_faellt_auf_nutzer_zurueck(self):
        for tab in (None, "", "<script>", "constructor", "__proto__"):
            with self.subTest(tab=tab):
                self.assertEqual(self.bereich(tab)["bereich"], "users")

    def test_eine_ebene_jeder_bereich_genau_einmal_mit_namen(self):
        alle = _ausfuehren("m.ADMIN_GRUPPEN.flatMap((g) => g.bereiche)")
        self.assertEqual(len(alle), len(set(alle)))
        namen = _ausfuehren("m.ADMIN_BEREICH_NAMEN")
        self.assertEqual(set(namen), set(alle))
        for pflicht in ("modelle", "sprache", "system", "integrationen-anlage"):
            self.assertIn(pflicht, alle)


if __name__ == "__main__":
    unittest.main()
