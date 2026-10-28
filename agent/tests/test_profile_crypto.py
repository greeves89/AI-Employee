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
import stat
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
        self.modul._SCHLUESSEL_DATEI = Path(os.path.join(self.tmp, "key"))
        self.modul._zwischengespeicherter_schluessel = None

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


class SchluesselAufloesung(ProfilCryptoBase):
    def test_persistierter_schluessel_wird_0600_angelegt(self):
        self._profil_anlegen()
        self.modul.verschluesseln(self.klartext, self.archiv)

        modus = stat.S_IMODE(os.stat(self.modul._SCHLUESSEL_DATEI).st_mode)
        self.assertEqual(modus, 0o600)

    def test_umgebung_schlaegt_persistierte_datei(self):
        from cryptography.fernet import Fernet

        vorgabe = Fernet.generate_key().decode()
        os.environ["BROWSER_PROFIL_KEY"] = vorgabe
        self.assertEqual(self.modul._schluessel().decode(), vorgabe)

    def test_ungueltiger_umgebungsschluessel_faellt_zurueck_statt_zu_crashen(self):
        """Ein Tippfehler in BROWSER_PROFIL_KEY darf den Agenten nicht aus dem
        Browser aussperren -- dieselbe Regel wie beim Orchestrator-Pendant."""
        os.environ["BROWSER_PROFIL_KEY"] = "offensichtlich-kein-fernet-schluessel"
        schluessel = self.modul._schluessel()
        from cryptography.fernet import Fernet
        Fernet(schluessel)  # wirft nicht -> ist ein gueltiger Fernet-Schluessel

    def test_gleicher_schluessel_ueber_zwei_prozesse_hinweg(self):
        """Simuliert einen Neustart: neuer Import, derselbe Schluesseldatei."""
        self._profil_anlegen("x")
        self.modul.verschluesseln(self.klartext, self.archiv)

        # ERST den Pfad als reinen Wert sichern: ``reload`` mutiert dasselbe
        # Modulobjekt in-place, ein Zugriff auf ``self.modul`` NACH dem
        # Reload saehe schon den zuruecksetzten Zustand.
        schluessel_pfad = self.modul._SCHLUESSEL_DATEI

        import app.profile_crypto as modul2
        modul2 = importlib.reload(modul2)
        modul2._SCHLUESSEL_DATEI = schluessel_pfad
        modul2._zwischengespeicherter_schluessel = None

        shutil.rmtree(self.klartext)
        modul2.entschluesseln(self.archiv, self.klartext)
        self.assertEqual(Path(self.klartext, "cookie.db").read_text(), "x")


if __name__ == "__main__":
    unittest.main()
