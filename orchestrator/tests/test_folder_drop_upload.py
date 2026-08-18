"""Dateien lassen sich auf einen Ordner im Dateibaum ziehen.

Wunsch des Nutzers vom 18.08.2026: „kann ich den linken bereich fuer uploads
auch per drag and drop einbinden? wenn ich eine datei auf einen ordner ziehe und
dann laedt der das da hoch?"

Es gibt ZWEI Dateibaeume — den im Arbeitsbereich eines Agenten und den
agentenuebergreifenden unter /files. Genau so eine Doppelung hat am selben Tag
schon einmal zugeschlagen: die Rueckfrage-Anzeige gab es dreimal, und beim
Anklickbarmachen wurde eine Fassung vergessen. Deshalb pruefen diese Tests
nicht, DASS es Abwurf-Behandler gibt, sondern dass beide Baeume denselben
benutzen.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HAKEN = (ROOT / "frontend/src/components/files/use-ordner-abwurf.ts").read_text()
AGENT = (ROOT / "frontend/src/app/agents/[id]/page.tsx").read_text()
DATEIEN = (ROOT / "frontend/src/app/files/page.tsx").read_text()


class BothTreesShareOneImplementationTests(unittest.TestCase):
    def test_the_agent_workspace_uses_the_shared_hook(self):
        self.assertIn("useOrdnerAbwurf", AGENT)

    def test_the_cross_agent_tree_uses_the_same_hook(self):
        self.assertIn("useOrdnerAbwurf", DATEIEN)

    def test_neither_tree_carries_its_own_copy(self):
        """Ein eigener ``dataTransfer``-Zugriff in einer der Seiten waere der
        Anfang der zweiten Fassung."""
        for name, quelle in (("agents/[id]", AGENT), ("files", DATEIEN)):
            with self.subTest(seite=name):
                self.assertNotIn("dataTransfer", quelle)


class DroppingActuallyUploadsTests(unittest.TestCase):
    def test_the_drop_reaches_the_upload_endpoint(self):
        self.assertIn("api.uploadFiles(agentId, pfad, dateien)", HAKEN)

    def test_a_file_drop_targets_its_folder(self):
        """Wer eine Datei knapp verfehlt, meint den Ordner, in dem sie liegt —
        sonst muesste man millimetergenau treffen."""
        for name, quelle in (("agents/[id]", AGENT), ("files", DATEIEN)):
            with self.subTest(seite=name):
                self.assertIn("const abwurfZiel = isDir ?", quelle)

    def test_only_real_file_drags_are_accepted(self):
        """Ohne diese Pruefung leuchtet der Baum auch beim Verschieben von
        markiertem Text auf."""
        self.assertIn('.includes("Files")', HAKEN)

    def test_dragging_a_whole_folder_is_refused_with_words(self):
        """Die Upload-Schnittstelle nimmt nur Dateien; ein Ordner kaeme als
        leere 0-Byte-Datei an und schluege unverstaendlich fehl."""
        self.assertIn("webkitGetAsEntry", HAKEN)
        self.assertIn("Ordner koennen nicht hochgeladen werden", HAKEN)

    def test_the_target_folder_is_reread_afterwards(self):
        """Sonst liegt die Datei da, ist aber nicht zu sehen."""
        self.assertIn("nachAbwurf(ziel)", HAKEN)
        for name, quelle in (("agents/[id]", AGENT), ("files", DATEIEN)):
            with self.subTest(seite=name):
                stelle = quelle.split("nachAbwurf: async (ziel)", 1)
                self.assertEqual(len(stelle), 2, "kein nachAbwurf gesetzt")
                self.assertIn("loadDir(", stelle[1][:400])

    def test_the_hovered_folder_is_marked(self):
        for name, quelle in (("agents/[id]", AGENT), ("files", DATEIEN)):
            with self.subTest(seite=name):
                self.assertIn("istAbwurfZiel &&", quelle)


if __name__ == "__main__":
    unittest.main()
