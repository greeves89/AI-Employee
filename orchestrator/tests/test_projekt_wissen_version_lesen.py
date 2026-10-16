"""Issue #968 Punkt 1: ``version_lesen`` gab einen Nicht-Dateiinhalt als Datei aus.

``git show <rev>:<pfad>`` antwortet mit Rückgabewert 0, wenn ``pfad`` kein
Dateiname ist — mit LEERER Ausgabe bei einem Musterzeichen (``*``, ``?``, ``[``)
und mit einer BAUMAUFLISTUNG bei leerem Pfad oder einem Ordner. Geprüft wurde
nur der Rückgabewert, also ging beides als „so sah die Datei damals aus" weiter;
``wissen_wiederherstellen`` hätte es in die Datei zurückgeschrieben.

Die Tests fahren echtes git über echte Dateien: ein Mock auf die Argumentliste
hätte nur die Schreibweise festgeschrieben, nicht das Verhalten.
"""

import tempfile
import unittest
from types import SimpleNamespace

from app.core import projekt_wissen, vault


class VersionLesenTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        vault.write_file(self.root, "ziel.md", "# ziel\n")
        vault.write_file(self.root, "nachbar.md", "# nachbar\n")
        vault.write_file(self.root, "leer.md", "")
        projekt_wissen._version_festhalten(self.root, "ziel.md", "Erster")
        for pfad in ("nachbar.md", "leer.md"):
            projekt_wissen._version_festhalten(self.root, pfad, "Erster")
        vault.write_file(self.root, "unter/tief.md", "# tief\n")
        projekt_wissen._version_festhalten(self.root, "unter/tief.md", "Erster")
        self.brain = SimpleNamespace(host_path=self.root)
        self.rev = vault.git_sicher(self.root, "rev-parse", "HEAD").stdout.strip()

    def test_der_echte_dateiinhalt_bleibt_lesbar(self):
        """Vorbedingung: ohne diese Zeile könnte jeder Test unten auch grün sein,
        weil ``version_lesen`` einfach immer wirft."""
        self.assertEqual(projekt_wissen.version_lesen(self.brain, "ziel.md", self.rev), "# ziel\n")
        self.assertEqual(projekt_wissen.version_lesen(self.brain, "unter/tief.md", self.rev), "# tief\n")

    def test_eine_wirklich_leere_datei_bleibt_leer_und_wirft_nicht(self):
        """Trennschärfe: „leere Ausgabe = Fehler" wäre der naheliegende, aber
        falsche Fix — eine legitim leere ``.md`` muss weiter lesbar sein."""
        self.assertEqual(projekt_wissen.version_lesen(self.brain, "leer.md", self.rev), "")

    def test_musterzeichen_im_pfad_ist_keine_leere_datei(self):
        for muster in ("*.md", "ziel?.md", "[zn]iel.md", "unter/*"):
            with self.subTest(pfad=muster):
                with self.assertRaises(FileNotFoundError):
                    projekt_wissen.version_lesen(self.brain, muster, self.rev)

    def test_leerer_pfad_liefert_nicht_die_baumauflistung(self):
        """Erreichbar über den Abfrageparameter ``pfad=`` von
        ``GET /teams/{id}/wissen/version``, der keine Mindestlänge hat."""
        for leer in ("", "   ", "/"):
            with self.subTest(pfad=leer):
                with self.assertRaises(FileNotFoundError):
                    projekt_wissen.version_lesen(self.brain, leer, self.rev)

    def test_ein_ordner_liefert_nicht_die_baumauflistung(self):
        with self.assertRaises(FileNotFoundError):
            projekt_wissen.version_lesen(self.brain, "unter", self.rev)

    def test_eine_damals_nicht_vorhandene_datei_bleibt_ein_fehler(self):
        with self.assertRaises(FileNotFoundError):
            projekt_wissen.version_lesen(self.brain, "gibtsnicht.md", self.rev)

    def test_unbekannte_version_bleibt_ein_wertfehler(self):
        for version in ("", "HEAD", "../..", "zzzz"):
            with self.subTest(version=version):
                with self.assertRaises(ValueError):
                    projekt_wissen.version_lesen(self.brain, "ziel.md", version)

    def test_ausbruch_aus_der_ablage_bleibt_ein_wertfehler(self):
        with self.assertRaises(ValueError):
            projekt_wissen.version_lesen(self.brain, "../../etc/passwd", self.rev)


if __name__ == "__main__":
    unittest.main()
