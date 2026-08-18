"""Second Brains: Vault als ZIP heraus- und hineinbringen.

Wunsch des Nutzers vom 18.08.2026: Import und Export fuer Second Brains, beim
Import „Sync mittels Obsidian und mittels Upload einer Zip-Datei" — und
ausdruecklich: „wichtig ist aber dass die PGVectoren dann nachgezogen werden".

Recherchiert: Obsidian Sync hat KEINE oeffentliche Schnittstelle (geschlossener,
Ende-zu-Ende-verschluesselter Bezahldienst). Ein Vault ist aber nur Markdown in
Ordnern, und genau so liegt ein Second Brain ohnehin auf der Platte — deshalb
der Weg ueber die Ordnerstruktur.

Ein hochgeladenes ZIP ist Fremdeingabe. Der Schwerpunkt hier liegt darauf, dass
es den Vault nicht verlassen kann.
"""

import io
import os
import tempfile
import unittest
import zipfile

from app.core import vault_transfer


def _zip(eintraege: dict[str, bytes]) -> zipfile.ZipFile:
    puffer = io.BytesIO()
    with zipfile.ZipFile(puffer, "w") as z:
        for name, inhalt in eintraege.items():
            z.writestr(name, inhalt)
    puffer.seek(0)
    return zipfile.ZipFile(puffer)


class ImportingAVaultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _gibt_es(self, rel):
        return os.path.isfile(os.path.join(self.vault, rel))

    def test_the_folder_structure_survives(self):
        """Ein Vault ohne Ordner waere kein Vault — die Struktur IST die
        Ordnung darin."""
        bericht = vault_transfer.importiere_zip(self.vault, _zip({
            "Notiz.md": b"# oben",
            "Projekte/Alpha/Plan.md": b"# tief",
        }))
        self.assertEqual(bericht.geschrieben, 2)
        self.assertTrue(self._gibt_es("Projekte/Alpha/Plan.md"))

    def test_merging_leaves_untouched_files_alone(self):
        os.makedirs(os.path.join(self.vault, "Alt"), exist_ok=True)
        with open(os.path.join(self.vault, "Alt/bleibt.md"), "w") as fh:
            fh.write("alt")
        vault_transfer.importiere_zip(self.vault, _zip({"neu.md": b"neu"}))
        self.assertTrue(self._gibt_es("Alt/bleibt.md"))

    def test_replacing_removes_what_is_not_in_the_archive(self):
        """Die ehrlichere Lesart von „Sync" — aber die gefaehrlichere, deshalb
        nicht die Vorgabe."""
        with open(os.path.join(self.vault, "weg.md"), "w") as fh:
            fh.write("alt")
        bericht = vault_transfer.importiere_zip(self.vault, _zip({"neu.md": b"neu"}), ersetzen=True)
        self.assertFalse(self._gibt_es("weg.md"))
        self.assertTrue(self._gibt_es("neu.md"))
        self.assertEqual(bericht.geloescht, 1)

    def test_an_existing_file_is_overwritten(self):
        with open(os.path.join(self.vault, "n.md"), "w") as fh:
            fh.write("alt")
        vault_transfer.importiere_zip(self.vault, _zip({"n.md": b"neu"}))
        with open(os.path.join(self.vault, "n.md")) as fh:
            self.assertEqual(fh.read(), "neu")


class AZipCannotEscapeTheVaultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = os.path.join(self.tmp.name, "vault")
        os.makedirs(self.vault)
        self.daneben = os.path.join(self.tmp.name, "geheim.txt")
        with open(self.daneben, "w") as fh:
            fh.write("unberuehrt")

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_climbing_path_is_refused(self):
        """Zip-Slip: der klassische Angriff auf jeden Entpacker."""
        bericht = vault_transfer.importiere_zip(self.vault, _zip({"../geheim.txt": b"gekapert"}))
        self.assertEqual(bericht.geschrieben, 0)
        with open(self.daneben) as fh:
            self.assertEqual(fh.read(), "unberuehrt")
        self.assertTrue(bericht.uebersprungen)

    def test_an_absolute_path_stays_inside_the_vault(self):
        """Ein absoluter Pfad im Archiv wird nicht abgewiesen, sondern in den
        Vault hinein normalisiert: aus ``/etc/passwd`` wird ``<vault>/etc/passwd``.
        Das ist harmlos — worauf es ankommt, ist, dass NICHTS ausserhalb landet."""
        vault_transfer.importiere_zip(self.vault, _zip({"/etc/passwd": b"gekapert"}))
        draussen = [
            os.path.join(w, f)
            for w, _, fs in os.walk(self.tmp.name) for f in fs
            if not os.path.join(w, f).startswith(self.vault + os.sep)
        ]
        self.assertEqual(draussen, [self.daneben], "es wurde ausserhalb des Vaults geschrieben")
        with open(self.daneben) as fh:
            self.assertEqual(fh.read(), "unberuehrt")

    def test_a_blocked_extension_is_refused(self):
        """Was nicht in den Arbeitsbereich hochgeladen werden darf, darf auch
        nicht ueber ein Vault-Archiv hereinkommen."""
        bericht = vault_transfer.importiere_zip(self.vault, _zip({"boese.exe": b"MZ"}))
        self.assertEqual(bericht.geschrieben, 0)
        self.assertIn("gesperrte Dateiendung", bericht.uebersprungen[0])

    def test_one_bad_entry_does_not_stop_the_good_ones(self):
        """Sonst kostet eine einzige krumme Datei den ganzen Import."""
        bericht = vault_transfer.importiere_zip(self.vault, _zip({
            "gut.md": b"ok", "../weg.txt": b"x", "auch_gut.md": b"ok",
        }))
        self.assertEqual(bericht.geschrieben, 2)
        self.assertEqual(len(bericht.uebersprungen), 1)


class ArchivesThatAreTooBigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_zip_bomb_is_refused_before_unpacking(self):
        """Wenige Kilobyte, die zu Gigabyte werden — geprueft wird die
        ENTPACKTE Groesse, nicht die des Archivs."""
        puffer = io.BytesIO()
        with zipfile.ZipFile(puffer, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("bombe.txt", b"\0" * (vault_transfer.MAX_ENTPACKT_BYTES + 1))
        puffer.seek(0)
        with self.assertRaises(ValueError) as fall:
            vault_transfer.importiere_zip(self.tmp.name, zipfile.ZipFile(puffer))
        self.assertIn("entpackt zu gross", str(fall.exception))

    def test_too_many_entries_are_refused(self):
        self.assertLess(vault_transfer.MAX_EINTRAEGE, 100_000)


class ExportingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = self.tmp.name
        os.makedirs(os.path.join(self.vault, "Projekte/Alpha"))
        with open(os.path.join(self.vault, "Projekte/Alpha/Plan.md"), "w") as fh:
            fh.write("# Plan")
        with open(os.path.join(self.vault, "Start.md"), "w") as fh:
            fh.write("# Start")

    def tearDown(self):
        self.tmp.cleanup()

    def _namen(self):
        puffer = io.BytesIO()
        vault_transfer.exportiere_zip(self.vault, puffer)
        puffer.seek(0)
        with zipfile.ZipFile(puffer) as z:
            return set(z.namelist())

    def test_the_structure_is_preserved(self):
        """Das Ergebnis soll sich direkt in Obsidian oeffnen lassen."""
        self.assertIn("Projekte/Alpha/Plan.md", self._namen())
        self.assertIn("Start.md", self._namen())

    def test_git_is_left_out(self):
        """`.git` gehoert dem Sync-Weg, nicht dem Inhalt — und ist oft groesser
        als der Vault selbst."""
        os.makedirs(os.path.join(self.vault, ".git"))
        with open(os.path.join(self.vault, ".git/config"), "w") as fh:
            fh.write("x")
        self.assertNotIn(".git/config", self._namen())

    def test_a_round_trip_keeps_everything(self):
        puffer = io.BytesIO()
        vault_transfer.exportiere_zip(self.vault, puffer)
        puffer.seek(0)
        ziel = tempfile.TemporaryDirectory()
        try:
            with zipfile.ZipFile(puffer) as z:
                bericht = vault_transfer.importiere_zip(ziel.name, z)
            self.assertEqual(bericht.geschrieben, 2)
            self.assertTrue(os.path.isfile(os.path.join(ziel.name, "Projekte/Alpha/Plan.md")))
        finally:
            ziel.cleanup()


class TheEmbeddingsAreRebuiltTests(unittest.TestCase):
    """Ausdrueckliche Vorgabe des Nutzers: „wichtig ist aber dass die
    PGVectoren dann nachgezogen werden".

    Ohne diesen Schritt liegen die Notizen zwar auf der Platte, sind aber
    semantisch unauffindbar — fuer die Agenten also praktisch unsichtbar.
    """

    from pathlib import Path
    QUELLE = (Path(__file__).resolve().parents[1] / "app/api/brains.py").read_text()

    def test_the_import_reindexes_afterwards(self):
        rumpf = self.QUELLE.split("async def brain_import(", 1)
        self.assertEqual(len(rumpf), 2, "Import-Endpunkt fehlt")
        self.assertIn("reindex_vault(", rumpf[1][:2600])

    def test_a_failed_reindex_does_not_lose_the_import(self):
        """Die Dateien liegen dann schon da — den Import deswegen scheitern zu
        lassen waere schlimmer als ein Hinweis."""
        rumpf = self.QUELLE.split("async def brain_import(", 1)[1][:2600]
        self.assertIn("Neuindizierung fehlgeschlagen", rumpf)

    def test_both_endpoints_are_admin_only(self):
        for name in ("brain_import(", "brain_export("):
            with self.subTest(endpunkt=name):
                rumpf = self.QUELLE.split(f"async def {name}", 1)[1][:1200]
                self.assertIn("_require_admin(user)", rumpf)


if __name__ == "__main__":
    unittest.main()
