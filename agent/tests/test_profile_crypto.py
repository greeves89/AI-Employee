"""Browserprofil verschluesselt ablegen (#828, Schritt 5).

Das persistente Chromium-Profil enthaelt nach einer Anmeldung faktisch
Zugangsdaten. Geprueft wird hier NUR das Ein-/Auspacken und die
Schluesselverwaltung — ohne Chromium, pure Dateioperationen. Der
Live-Nachweis, dass das Profil nach Neustart angemeldet bleibt, steht in
``test_browser_profil_und_tabs.py`` (braucht echtes Chromium).
"""

import importlib
import os
import shutil
import tempfile
import unittest
from pathlib import Path


class ProfilCryptoBase(unittest.TestCase):
    def setUp(self):
        import app.profile_crypto as modul
        self.modul = importlib.reload(modul)

        self.tmp = tempfile.mkdtemp(prefix="profilcrypto-")
        self.klartext = os.path.join(self.tmp, "klartext")
        self.archiv = os.path.join(self.tmp, "archiv.tar.enc")
        from cryptography.fernet import Fernet

        os.environ["BROWSER_PROFIL_KEY"] = Fernet.generate_key().decode()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("BROWSER_PROFIL_KEY", None)

    def _profil_anlegen(self, inhalt: str = "geheime-sitzung") -> None:
        os.makedirs(self.klartext, exist_ok=True)
        with open(os.path.join(self.klartext, "cookie.db"), "w") as f:
            f.write(inhalt)


class Roundtrip(ProfilCryptoBase):
    def test_entschluesseln_liefert_denselben_inhalt(self):
        self._profil_anlegen("geheime-sitzung-123")
        self.modul.verschluesseln(self.klartext, self.archiv)

        shutil.rmtree(self.klartext)
        self.modul.entschluesseln(self.archiv, self.klartext)

        self.assertEqual(
            Path(self.klartext, "cookie.db").read_text(), "geheime-sitzung-123")

    def test_archiv_ist_wirklich_verschluesselt(self):
        """Nicht nur behauptet — das Klartext-Geheimnis darf in der
        abgelegten Datei nirgends als Byte-Folge vorkommen."""
        self._profil_anlegen("geheime-sitzung-123")
        self.modul.verschluesseln(self.klartext, self.archiv)

        roh = Path(self.archiv).read_bytes()
        self.assertNotIn(b"geheime-sitzung-123", roh)

    def test_ohne_archiv_startet_leer_statt_zu_scheitern(self):
        """Erster Lauf eines Agenten: noch kein Archiv vorhanden."""
        self.modul.entschluesseln(self.archiv, self.klartext)
        self.assertTrue(os.path.isdir(self.klartext))
        self.assertEqual(os.listdir(self.klartext), [])

    def test_leeres_verzeichnis_wird_nicht_gesichert(self):
        """Ein nie benutztes Profil erzeugt kein Archiv -- nichts zu verlieren,
        nichts, was ein spaeteres echtes Profil ueberschreiben koennte."""
        self.modul.verschluesseln(self.klartext, self.archiv)
        self.assertFalse(os.path.exists(self.archiv))


class Idempotenz(ProfilCryptoBase):
    def test_zweiter_entschluesseln_aufruf_ueberschreibt_nicht(self):
        """Zwei Aufrufer (Python-Werkzeug UND Playwright-MCP-Registrierung)
        rufen potenziell beide ``sicherstellen_profil_entschluesselt`` -- der
        zweite darf ein inzwischen von Chromium beschriebenes Profil nicht
        mit dem alten Archivstand ueberschreiben."""
        self._profil_anlegen("archiv-stand")
        self.modul.verschluesseln(self.klartext, self.archiv)

        with open(os.path.join(self.klartext, "cookie.db"), "w") as f:
            f.write("inzwischen-veraendert")

        self.modul.entschluesseln(self.archiv, self.klartext)
        self.assertEqual(
            Path(self.klartext, "cookie.db").read_text(), "inzwischen-veraendert")


class Schluessel(ProfilCryptoBase):
    def test_kein_schluessel_auf_der_platte(self):
        """Der Schluessel kommt vom Orchestrator; neben dem Archiv liegt nichts."""
        self._profil_anlegen()
        self.modul.verschluesseln(self.klartext, self.archiv)
        self.assertEqual(sorted(os.listdir(self.tmp)), ["archiv.tar.enc", "klartext"])

    def test_ohne_schluessel_wird_nichts_gesichert(self):
        os.environ.pop("BROWSER_PROFIL_KEY")
        self._profil_anlegen()
        with self.assertRaises(self.modul.KeinSchluessel):
            self.modul.verschluesseln(self.klartext, self.archiv)
        self.assertFalse(os.path.exists(self.archiv))

    def test_ohne_schluessel_bleibt_das_archiv_unberuehrt(self):
        self._profil_anlegen("alt")
        self.modul.verschluesseln(self.klartext, self.archiv)
        vorher = Path(self.archiv).read_bytes()
        shutil.rmtree(self.klartext)

        os.environ["BROWSER_PROFIL_KEY"] = "kein-fernet-schluessel"
        self.modul.entschluesseln(self.archiv, self.klartext)
        self.assertEqual(Path(self.archiv).read_bytes(), vorher)
        self.assertEqual(os.listdir(self.klartext), [])

    def test_falscher_schluessel_legt_das_archiv_beiseite(self):
        """Nicht ueberschreiben: Mit dem richtigen Schluessel ist es noch lesbar."""
        from cryptography.fernet import Fernet

        self._profil_anlegen("alt")
        self.modul.verschluesseln(self.klartext, self.archiv)
        vorher = Path(self.archiv).read_bytes()
        shutil.rmtree(self.klartext)

        os.environ["BROWSER_PROFIL_KEY"] = Fernet.generate_key().decode()
        self.modul.entschluesseln(self.archiv, self.klartext)
        self.assertFalse(os.path.exists(self.archiv))
        beiseite = [n for n in os.listdir(self.tmp) if ".nicht-lesbar-" in n]
        self.assertEqual(len(beiseite), 1)
        self.assertEqual(Path(self.tmp, beiseite[0]).read_bytes(), vorher)


class AltesProfil(ProfilCryptoBase):
    """Bis v1.387 lag das Profil im Klartext im Arbeitsbereich."""

    def setUp(self):
        super().setUp()
        import app.tools.browser as browser
        self.browser = importlib.reload(browser)
        self.alt = os.path.join(self.tmp, "alt-profil")
        self.browser.ALTES_PROFIL_DIR = self.alt
        self.browser.PROFIL_ARCHIV = self.archiv
        self.browser.PROFIL_DIR = self.klartext
        os.makedirs(self.alt)
        Path(self.alt, "cookie.db").write_text("alte-anmeldung")

    def test_wird_uebernommen_und_der_klartext_geloescht(self):
        self.browser.sicherstellen_profil_entschluesselt()
        self.assertFalse(os.path.exists(self.alt))
        self.assertEqual(Path(self.klartext, "cookie.db").read_text(), "alte-anmeldung")
        self.assertNotIn(b"alte-anmeldung", Path(self.archiv).read_bytes())

    def test_ohne_schluessel_bleibt_es_liegen(self):
        os.environ.pop("BROWSER_PROFIL_KEY")
        self.browser.sicherstellen_profil_entschluesselt()
        self.assertTrue(os.path.exists(self.alt))
        self.assertFalse(os.path.exists(self.archiv))


if __name__ == "__main__":
    unittest.main()
