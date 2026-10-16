"""Issue #964, Punkte 2 und 3: der Dateiname einer Ablage geht an git weiter.

``--`` schaltet die Options-Erkennung ab, nicht die Pathspec-Magie. Ein Name wie
``:(glob)**/*.md`` passiert sowohl die Pfadpruefung als auch die Suffixpruefung
der Ablage und nahm im anschliessenden Commit danach *jede* Datei der Ablage auf
— der Commit lautete aber auf eine einzelne Datei und einen einzelnen Autor.
Kein Ausbruch aus der Ablage (der Aufrufer darf sie ohnehin lesen), also ein
Korrektheits- und kein Sicherheitsbefund: der Versionsverlauf wird falsch.

Die Tests fahren echtes git ueber echte Dateien — ein Mock auf die Argumentliste
haette nur die Schreibweise festgeschrieben, nicht das Verhalten.
"""

import os
import subprocess
import tempfile
import unittest
from types import SimpleNamespace

from app.core import projekt_wissen, vault


def dateien_im_commit(host_path: str, rev: str = "HEAD") -> set[str]:
    r = vault.git_sicher(host_path, "show", "--name-only", "--format=", rev)
    return {z.strip() for z in r.stdout.splitlines() if z.strip()}


class PfadspekTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        vault.write_file(self.root, "harmlos.md", "# harmlos\n")
        vault.write_file(self.root, "geheim.md", "# nicht meine Datei\n")
        projekt_wissen._version_festhalten(self.root, "harmlos.md", "Erster")
        # Ein zweiter Stand, damit es ueberhaupt etwas zu vermischen gibt.
        vault.write_file(self.root, "ziel.md", "# ziel\n")

    def test_pathspec_magie_nimmt_keine_fremden_dateien_in_den_commit(self):
        projekt_wissen._version_festhalten(self.root, ":(glob)**/*.md", "Angreifer")

        dateien = dateien_im_commit(self.root)
        self.assertNotIn("geheim.md", dateien,
                         f"fremde Datei im Commit: {sorted(dateien)}")

    def test_der_gewollte_einzelne_pfad_wird_weiterhin_festgehalten(self):
        projekt_wissen._version_festhalten(self.root, "ziel.md", "Zweiter")

        self.assertEqual(dateien_im_commit(self.root), {"ziel.md"})

    def test_verlauf_liest_nur_den_angefragten_pfad(self):
        projekt_wissen._version_festhalten(self.root, "ziel.md", "Zweiter")
        brain = SimpleNamespace(host_path=self.root)

        # ``:(exclude)`` liefert sonst den Verlauf aller ANDEREN Dateien.
        eintraege = projekt_wissen.verlauf(brain, ":(exclude)ziel.md")

        self.assertEqual(eintraege, [])

    def test_verlauf_des_echten_pfades_bleibt_lesbar(self):
        projekt_wissen._version_festhalten(self.root, "ziel.md", "Zweiter")
        brain = SimpleNamespace(host_path=self.root)

        eintraege = projekt_wissen.verlauf(brain, "ziel.md")

        self.assertEqual(len(eintraege), 1)
        self.assertEqual(eintraege[0]["wer"], "Zweiter")

    def test_limit_wird_nicht_als_git_option_durchgereicht(self):
        projekt_wissen._version_festhalten(self.root, "ziel.md", "Zweiter")
        brain = SimpleNamespace(host_path=self.root)

        erwartet = projekt_wissen.verlauf(brain, "ziel.md")
        self.assertEqual(len(erwartet), 1)  # Vorbedingung: es gibt etwas zu verlieren

        # Trennschaerfe: "liefert eine Liste" waere auch wahr, wenn die Option
        # durchschlaegt — dann ist die Liste nur LEER. Gemessen wird deshalb gegen
        # den Verlauf, den der Standardaufruf liefert.
        for boese in ("-format=%H", "x", None, -5):
            with self.subTest(limit=boese):
                self.assertEqual(projekt_wissen.verlauf(brain, "ziel.md", limit=boese),
                                 erwartet)

    def test_leerer_pfad_bedeutet_nicht_die_ganze_ablage(self):
        """``":(literal)"`` allein ist fuer git „alles" — das nackte ``""`` war noch
        ein ``fatal``. Erreichbar ueber den Abfrageparameter ``pfad=`` des Verlaufs,
        der keine Mindestlaenge hat."""
        projekt_wissen._version_festhalten(self.root, "ziel.md", "Zweiter")
        brain = SimpleNamespace(host_path=self.root)

        for leer in ("", "   "):
            with self.subTest(pfad=leer):
                self.assertEqual(projekt_wissen.verlauf(brain, leer), [])

    def test_leerer_pfad_schreibt_keinen_sammelcommit(self):
        vor = vault.git_sicher(self.root, "rev-parse", "HEAD").stdout.strip()

        projekt_wissen._version_festhalten(self.root, "", "Angreifer")

        nach = vault.git_sicher(self.root, "rev-parse", "HEAD").stdout.strip()
        self.assertEqual(vor, nach, "leerer Pfad hat einen Commit erzeugt")

    def test_limit_begrenzt_weiterhin_die_anzahl(self):
        brain = SimpleNamespace(host_path=self.root)
        for i in range(3):
            vault.write_file(self.root, "ziel.md", f"# stand {i}\n")
            projekt_wissen._version_festhalten(self.root, "ziel.md", f"Autor{i}")

        self.assertEqual(len(projekt_wissen.verlauf(brain, "ziel.md", limit=2)), 2)
        self.assertEqual(len(projekt_wissen.verlauf(brain, "ziel.md", limit=30)), 3)


if __name__ == "__main__":
    unittest.main()
