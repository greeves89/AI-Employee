"""Die Datei-Landkarte sagt dem Agenten, WAS im Vault liegt — mit Deckel.

Dieser Block geht in JEDEN Prompt. Ohne harte Obergrenze waechst er mit dem
Vault, und ein Abteilungs-Vault mit einigen tausend Artikeln verdraengt irgendwann
den Auftrag selbst. Deshalb pruefen diese Tests nicht die Formulierung, sondern
genau die drei Entscheidungen: vollstaendige Liste, Verzeichnis-Uebersicht,
Kuerzung — und dass die Kuerzung als solche erkennbar ist.

Warum die Landkarte ueberhaupt noetig ist, obwohl es eine Suche gibt: Struktur
traegt Bedeutung. Ein Ordnername (`Zugaenge/`, `Drucker/`) ist schon eine
Aussage, und genau die geht beim Chunking verloren.
"""
import os
import tempfile
import unittest
from unittest import mock

from app import runner_hooks as rh


def _vault(tmp: str, slug: str, pfade: list[str]) -> str:
    d = os.path.join(tmp, slug)
    for p in pfade:
        ziel = os.path.join(d, p)
        os.makedirs(os.path.dirname(ziel), exist_ok=True)
        with open(ziel, "w") as f:
            f.write("# " + os.path.basename(p))
    os.makedirs(d, exist_ok=True)
    return d


class LandkarteTests(unittest.TestCase):

    def test_unter_der_schwelle_die_vollstaendige_liste(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = _vault(tmp, "it", ["Drucker/x17137.md", "Netzwerk/vpn.md", "index.md"])
            karte = rh.get_vault_file_map([d])
        self.assertIn("Drucker/x17137.md", karte)
        self.assertIn("Netzwerk/vpn.md", karte)
        self.assertIn("(3 Dateien)", karte)
        self.assertNotIn("Verzeichnis-Uebersicht", karte)

    def test_ueber_der_schwelle_die_verzeichnis_uebersicht_mit_zaehlern(self):
        with tempfile.TemporaryDirectory() as tmp:
            viele = [f"Drucker/a{i:03d}.md" for i in range(rh.VAULT_MAP_MAX_FILES + 5)]
            viele += ["Netzwerk/vpn.md"]
            d = _vault(tmp, "it", viele)
            karte = rh.get_vault_file_map([d])
        self.assertIn("Verzeichnis-Uebersicht", karte)
        self.assertIn(f"({rh.VAULT_MAP_MAX_FILES + 5})", karte)   # Zaehler je Ordner
        self.assertIn("Netzwerk/  (1)", karte)
        self.assertNotIn("a000.md", karte)                        # keine Einzeldateien

    def test_zeichen_obergrenze_wird_eingehalten_und_gekennzeichnet(self):
        """Der Deckel gilt ueber ALLE Vaults zusammen, nicht je Vault — sonst
        skaliert der Block mit der Zahl der Zuweisungen."""
        with tempfile.TemporaryDirectory() as tmp:
            dirs = []
            for v in range(6):
                # Je Vault unter der Dateischwelle, zusammen weit ueber dem
                # Zeichendeckel: lange Pfade, 100 Dateien.
                pfade = [f"Sehr/Tiefer/Ordner/Mit/Langem/Namen/artikel-{i:03d}-ausfuehrlich.md"
                         for i in range(100)]
                dirs.append(_vault(tmp, f"v{v}", pfade))
            karte = rh.get_vault_file_map(dirs)
        self.assertLessEqual(len(karte), rh.VAULT_MAP_MAX_CHARS + 80)
        self.assertIn("gekuerzt", karte)
        self.assertIn("secondbrain_list", karte)   # der Weg zum vollen Stand

    def test_leerer_vault_wird_als_leer_benannt(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = _vault(tmp, "leer", [])
            karte = rh.get_vault_file_map([d])
        self.assertIn("(leer)", karte)

    def test_ohne_vault_kein_block(self):
        """Heutiges Verhalten bleibt: kein gemounteter Brain, kein Abschnitt."""
        self.assertEqual(rh.get_vault_file_map([]), "")

    def test_nur_durchsuchbare_dateien(self):
        """Ein Vault ist eine Markdown-Sammlung — Bilder und .git gehoeren nicht
        in einen Block, der in jeden Prompt geht."""
        with tempfile.TemporaryDirectory() as tmp:
            d = _vault(tmp, "it", ["a.md", "bild.png", "notiz.txt", ".git/config"])
            karte = rh.get_vault_file_map([d])
        self.assertIn("a.md", karte)
        self.assertIn("notiz.txt", karte)
        self.assertNotIn("bild.png", karte)
        self.assertNotIn(".git", karte)

    def test_ein_kaputter_pfad_kippt_den_prompt_nicht(self):
        """Lieber ein knapper Block als ein Startfehler.

        Der Aufrufer uebergibt nur existierende Verzeichnisse (``glob`` +
        ``isdir``), aber dieser Block laeuft bei JEDEM Start — er darf unter
        keinen Umstaenden werfen. Ein nicht lesbarer Pfad sieht hier aus wie ein
        leerer Vault, und das ist die richtige Wahl: eine Zeile im Prompt statt
        eines Agenten, der nicht hochkommt.
        """
        karte = rh.get_vault_file_map(["/gibt/es/nicht/sicher"])
        self.assertIn("(leer)", karte)
        self.assertLess(len(karte), 200)


class LandkarteIstVerdrahtetTests(unittest.TestCase):
    """Die Landkarte selbst ist oben geprueft — hier geht es darum, dass sie
    ueberhaupt im Prompt landet. Eine gut getestete Funktion, die niemand
    aufruft, ist toter Code; genau diese Klasse ist in diesem Issue schon
    einmal beim MCP-Server zugeschlagen."""

    def test_der_mount_abschnitt_haengt_die_landkarte_ein(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = _vault(tmp, "it", ["Drucker/x17137.md", "index.md"])
            # ``get_mounts_context`` liest /mnt/brains per glob — das Modul
            # importiert glob lokal, also wird die Suchfunktion selbst ersetzt.
            import glob as _glob
            echt = _glob.glob

            def gefaelscht(muster):
                if muster == "/mnt/brains/*":
                    return [d]
                if muster == "/mnt/*":
                    return [d]
                return echt(muster)

            with mock.patch.object(_glob, "glob", gefaelscht):
                block = rh.get_mounts_context()

        self.assertIn("Second Brain", block)
        self.assertIn("Landkarte", block)
        self.assertIn("Drucker/x17137.md", block)

    def test_ohne_vault_steht_keine_landkarte_im_prompt(self):
        import glob as _glob
        with mock.patch.object(_glob, "glob", lambda muster: []):
            self.assertEqual(rh.get_mounts_context(), "")


if __name__ == "__main__":
    unittest.main()
